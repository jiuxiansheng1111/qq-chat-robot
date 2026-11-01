"""Prepare user-supplied recordings for local GPT-SoVITS training.

Whole recordings are scanned. Speech cuts and ASR decisions retain source offsets;
original media is never edited. Each completed step can be resumed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path


def save_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def decode(ffmpeg: Path, source: Path, target: Path, rate: int) -> None:
    if target.exists():
        return
    subprocess.run([str(ffmpeg), "-v", "error", "-nostdin", "-y", "-i", str(source),
                    "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", str(target)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--sample-only", action="store_true")
    parser.add_argument("--roles", nargs="*")
    args = parser.parse_args()
    import numpy as np
    import soundfile as sf
    import torch
    from funasr import AutoModel

    torch.set_num_threads(2)
    job = args.job
    sources = json.loads((job / "sources.json").read_text(encoding="utf-8"))
    if args.roles:
        sources = [s for s in sources if s["role"] in args.roles]
    ffmpeg = Path("data/singing/runtime/bin/ffmpeg.exe").absolute()
    os.environ["PATH"] = str(ffmpeg.parent) + os.pathsep + os.environ.get("PATH", "")
    vad_path = args.model_cache / "models/iic--speech_fsmn_vad_zh-cn-16k-common-pytorch/snapshots/master"
    asr_path = args.model_cache / "models/iic--SenseVoiceSmall/snapshots/master"
    vad = None
    prepared = []
    for source in sources:
        role = source["role"]
        corpus_id = source.get("corpus_id", role)
        output = job / corpus_id
        output.mkdir(exist_ok=True)
        media = Path(source["processed_audio"])
        assert hashlib.sha256(Path(source["original"]).read_bytes()).hexdigest() == source["sha256"]
        pcm16 = output / "source16k.wav"
        pcm32 = output / "source32k.wav"
        audio_dir = output / ("sample_audio" if args.sample_only else "audio")
        audio_dir.mkdir(exist_ok=True)
        cut_file = output / ("sample-cuts.json" if args.sample_only else "cuts.json")
        if source.get("presegmented_cuts") and not args.sample_only:
            cuts = json.loads(Path(source["presegmented_cuts"]).read_text(encoding="utf-8"))
            save_json(cut_file, cuts)
        elif cut_file.exists():
            cuts = json.loads(cut_file.read_text(encoding="utf-8"))
        else:
            decode(ffmpeg, media, pcm16, 16000)
            decode(ffmpeg, media, pcm32, 32000)
            info = sf.info(pcm16)
            if args.sample_only:
                length = info.duration
                intervals = [[int(max(0, min(length-12, t))*1000),
                              int(min(length, max(0, min(length-12, t))+12)*1000)]
                             for t in (10, length*0.25, length*0.50, length*0.75, length-30)]
            else:
                if vad is None:
                    vad = AutoModel(model=str(vad_path), device="cpu", disable_update=True,
                                    disable_pbar=True, disable_log=True)
                result = vad.generate(input=str(pcm16), cache={}, disable_pbar=True,
                                      max_single_segment_time=12000)
                intervals = result[0]["value"]
                save_json(output / "vad.json", result)
            cuts = []
            seen = set()
            with sf.SoundFile(pcm32) as audio:
                for start_ms, end_ms in intervals:
                    # Keep natural pauses; cap long uninterrupted turns for the model.
                    start = max(0, int(start_ms*32)-2560)
                    end = min(len(audio), int(end_ms*32)+3840)
                    while start < end:
                        stop = min(end, start+12*32000)
                        if stop < end:
                            audio.seek(max(start, stop-32000))
                            tail = audio.read(stop-max(start, stop-32000), dtype="float32")
                            windows = [np.mean(tail[i:i+640]**2) for i in range(0, len(tail)-640, 640)]
                            if windows:
                                stop = max(start+32000, stop-32000+int(np.argmin(windows))*640+320)
                        audio.seek(start)
                        pcm = audio.read(stop-start, dtype="int16")
                        duration = len(pcm)/32000
                        start_here = start
                        start = stop
                        if duration < 0.65 or not len(pcm) or np.sqrt(np.mean(pcm.astype("float64")**2)) < 10:
                            continue
                        digest = hashlib.sha256(pcm.tobytes()).hexdigest()
                        if digest in seen:
                            continue
                        seen.add(digest)
                        path = audio_dir / f"{corpus_id}_{len(cuts)+1:05d}.wav"
                        sf.write(path, pcm, 32000, subtype="PCM_16")
                        cuts.append({"file":str(path), "source_start":round(start_here/32000,3),
                                     "source_end":round(stop/32000,3), "seconds":round(duration,3),
                                     "pcm_sha256":digest})
            save_json(cut_file, cuts)
        prepared.append((source, output, cuts))
        print(json.dumps({"role":role,"stage":"cuts_ready","cuts":len(cuts),
                          "seconds":round(sum(c["seconds"] for c in cuts),2)}), flush=True)
    del vad
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    asr = AutoModel(model=str(asr_path), device=args.device, disable_update=True,
                    disable_pbar=True, disable_log=True)
    tag_re = re.compile(r"<\|([^|]+)\|>")
    for source, output, cuts in prepared:
        journal = output / ("sample-asr.jsonl" if args.sample_only else "asr.jsonl")
        rows = {}
        if journal.exists():
            for line in journal.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                rows[row["pcm_sha256"]] = row
        started = time.monotonic()
        with journal.open("a", encoding="utf-8") as stream:
            for index, cut in enumerate(cuts):
                if cut["pcm_sha256"] in rows:
                    continue
                result = asr.generate(input=cut["file"], language="auto", use_itn=False,
                                      disable_pbar=True, batch_size_s=30)
                raw = str(result[0].get("text", "")) if result else ""
                tags = [m.casefold() for m in tag_re.findall(raw)]
                text = tag_re.sub("", raw).replace("|", "，").replace("\t", " ").replace("\n", " ").strip()
                language = next((t for t in tags if t in {"ja","zh","en","yue","ko"}), "unknown")
                valid = bool(text) and language in {"ja","zh","en","yue"} and "nospeech" not in tags
                reason = "accepted_asr" if valid else "no_reliable_lexical_speech"
                if language not in source.get("allowed_languages", ["ja","zh","en","yue"]):
                    valid, reason = False, "unexpected_source_language"
                if len(text) > max(25, cut["seconds"]*18):
                    valid, reason = False, "implausible_transcript_density"
                if re.search(r"(.)\1{9,}", text):
                    valid, reason = False, "asr_repetition"
                row = {**cut,"language":language,"text":text,"asr_raw":raw,"tags":tags,
                       "accepted":valid,"decision":reason}
                rows[cut["pcm_sha256"]] = row
                stream.write(json.dumps(row,ensure_ascii=False)+"\n")
                stream.flush()
                if index % 30 == 0 or index+1 == len(cuts):
                    print(json.dumps({"role":source["role"],"stage":"asr", "done":index+1,
                                      "total":len(cuts),"elapsed":round(time.monotonic()-started,1)}),flush=True)
        ordered = [rows[c["pcm_sha256"]] for c in cuts]
        accepted = [r for r in ordered if r["accepted"]]
        summary = {"role":source["role"],"original_sha256":source["sha256"],
                   "cuts":len(cuts),"accepted_cuts":len(accepted),
                   "accepted_seconds":round(sum(r["seconds"] for r in accepted),2),
                   "music_tagged_cuts":sum("bgm" in r["tags"] for r in ordered),
                   "languages":{lang:sum(r["language"]==lang for r in accepted)
                                for lang in sorted({r["language"] for r in accepted})}}
        save_json(output / ("sample-summary.json" if args.sample_only else "summary.json"), summary)
        if not args.sample_only:
            save_json(output / "asr.json", ordered)
            (output / "training.list").write_text("\n".join(
                f"{r['file']}|{source['role']}|{r['language']}|{r['text']}" for r in accepted)+"\n",encoding="utf-8")
        print(json.dumps(summary),flush=True)


if __name__ == "__main__":
    main()
