"""Independent local Whisper review; keeps raw hypotheses and source offsets."""
import argparse
import json
import time
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--job",type=Path,required=True)
    parser.add_argument("--model",type=Path,required=True)
    parser.add_argument("--device",default="cuda")
    parser.add_argument("--roles",nargs="+",default=["murasame","yoshino"])
    options=parser.parse_args()
    import pyopenjtalk
    import soundfile as sf
    from faster_whisper import WhisperModel

    job=options.job
    dictionary=Path(pyopenjtalk.OPEN_JTALK_DICT_DIR.decode("utf-8"))
    workspace=Path(__file__).resolve().parents[2]
    if dictionary.is_relative_to(workspace) and job.drive:
        alias=Path(job.drive+"/")/dictionary.relative_to(workspace)
        if alias.is_dir():
            pyopenjtalk.OPEN_JTALK_DICT_DIR=str(alias).encode("utf-8")
    model=WhisperModel(str(options.model),device=options.device,
                       compute_type="int8_float16" if options.device=="cuda" else "int8",
                       cpu_threads=2,num_workers=1,local_files_only=True)
    tasks=[]
    if "murasame" in options.roles:
        output=job/"chinese-review"
        output.mkdir(exist_ok=True)
        windows=[(3.98,9.34),(38.15,41.3),(42.45,46.2),(48.9,52.4),
                 (66.81,74.47),(70.32,74.0),(74.57,77.98),(80.65,83.12),(112.98,118.07)]
        with sf.SoundFile(job/"murasame/source32k.wav") as audio:
            for index,(start,end) in enumerate(windows,1):
                path=output/f"cn-{index:02d}.wav"
                audio.seek(int(start*32000))
                pcm=audio.read(int((end-start)*32000),dtype="int16")
                sf.write(path,pcm,32000,subtype="PCM_16")
                tasks.append({"file":str(path),"role":"murasame","language":"zh",
                              "source_start":start,"source_end":end})
        for row in json.loads((job/"murasame_ja/asr.json").read_text(encoding="utf-8")):
            if row["accepted"]:
                tasks.append({**row,"role":"murasame","corpus":"murasame_ja"})
    for role in [r for r in options.roles if r!="murasame"]:
        corpus=job/("mako_clean" if role=="mako" else role)
        for row in json.loads((corpus/"asr.json").read_text(encoding="utf-8")):
            if row["accepted"] and row["language"]=="ja":
                tasks.append({**row,"role":role,"corpus":corpus.name})
    target=job/"whisper-review.jsonl"
    done={}
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            row=json.loads(line);done[row["file"]]=row
    started=time.monotonic()
    with target.open("a",encoding="utf-8") as output:
        for index,task in enumerate(tasks,1):
            if task["file"] in done:continue
            segments,_info=model.transcribe(task["file"],language=task["language"],beam_size=5,
                condition_on_previous_text=False,vad_filter=False,temperature=0,
                without_timestamps=True)
            segments=list(segments)
            text="".join(s.text for s in segments).strip()
            row={**task,"whisper_text":text,
                 "whisper_avg_logprob":sum(s.avg_logprob for s in segments)/max(1,len(segments)),
                 "whisper_no_speech_prob":max((s.no_speech_prob for s in segments),default=1.0),
                 "whisper_language_forced":task["language"],"whisper_device":options.device}
            if task["language"]=="ja":
                def phones(value):
                    return [p for p in pyopenjtalk.g2p(value).split() if p not in {"sil","pau"}]
                reference=phones(task["text"]);hypothesis=phones(text)
                previous=list(range(len(hypothesis)+1))
                for i,a in enumerate(reference,1):
                    current=[i]
                    for j,b in enumerate(hypothesis,1):
                        current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(a!=b)))
                    previous=current
                row["phoneme_disagreement"]=round(previous[-1]/max(1,len(reference)),4)
            done[task["file"]]=row
            output.write(json.dumps(row,ensure_ascii=False)+"\n");output.flush()
            if index%30==0 or index==len(tasks):
                print(json.dumps({"stage":"independent_ASR_review","done":index,"total":len(tasks),
                                  "elapsed":round(time.monotonic()-started,1)}),flush=True)
    print(json.dumps({"reviewed":len(tasks),"journal":str(target)}),flush=True)


if __name__=="__main__":
    main()
