"""Measure reference pitch and phonation; preserve all audio and transcript provenance."""
import argparse
import json
import re
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source-job",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    import numpy as np
    import soundfile as sf
    import librosa
    from scipy.signal import resample_poly
    source=args.source_job.resolve();output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    workspace=source.parents[4]
    def physical(value):
        return workspace/Path(value[3:]) if re.match(r"(?i)^R:[/\\]",value) else Path(value)
    reviewed={Path(row["file"]).name:row for row in map(json.loads,(source/"whisper-review.jsonl").read_text(encoding="utf-8").splitlines()) if row.get("role")=="yoshino"}
    corpus=json.loads((source/"yoshino/asr.json").read_text(encoding="utf-8"))
    reference=json.loads((source/"yoshino-clean-reference.json").read_text(encoding="utf-8"))
    targets=[]
    for row in corpus:
        review=reviewed.get(Path(row["file"]).name,{})
        if row["language"]!="ja" or not row["accepted"]:continue
        if not 3.4<=row["seconds"]<=8.0 and Path(row["file"]).name!=Path(reference["file"]).name:continue
        if review.get("phoneme_disagreement",1)>.15:continue
        if review.get("whisper_avg_logprob",-10)<-.65:continue
        if review.get("whisper_no_speech_prob",1)>.2:continue
        if re.search("性|裸|エッチ|おっぱい|乳|尻|キス|脱|下着|風呂|嫌|やめ|痛|泣",row["text"]):continue
        targets.append({**row,"review":review})
    if not any(Path(row["file"]).name==Path(reference["file"]).name for row in targets):
        original=next(row for row in corpus if Path(row["file"]).name==Path(reference["file"]).name)
        targets.append({**original,"review":reviewed.get(Path(original["file"]).name,{})})
    output_file=output/"reference-audit.jsonl"
    done={}
    if output_file.exists():
        done={row["file"]:row for row in map(json.loads,output_file.read_text(encoding="utf-8").splitlines())}
    with output_file.open("a",encoding="utf-8") as journal:
        for index,row in enumerate(targets,1):
            file=physical(row["file"])
            if str(file) in done:continue
            audio,sr=sf.read(file,dtype="float32")
            if audio.ndim>1:audio=audio.mean(axis=1)
            if sr!=16000:audio=resample_poly(audio,16000,sr).astype("float32");sr=16000
            frame_length=1024;hop=256
            frames=librosa.util.frame(np.pad(audio,(512,512)),frame_length=frame_length,hop_length=hop)
            rms=np.sqrt(np.mean(frames**2,axis=0))
            f0=librosa.yin(audio,fmin=95,fmax=650,sr=sr,frame_length=frame_length,hop_length=hop)
            count=min(len(f0),len(rms));rms=rms[:count];f0=f0[:count]
            lags=np.rint(sr/f0).astype(int)
            periodic=[]
            for i,lag in enumerate(lags):
                frame=frames[:,i]
                a=frame[:-lag];b=frame[lag:]
                periodic.append(float(np.dot(a,b)/np.sqrt(np.dot(a,a)*np.dot(b,b)+1e-12)))
            periodic=np.asarray(periodic)
            voiced=(rms>max(.004,float(np.quantile(rms,.8))*.25))&(periodic>.65)
            if not np.any(voiced):continue
            spectrum=np.abs(np.fft.rfft(frames,axis=0))**2
            frequencies=np.fft.rfftfreq(frame_length,1/sr)
            low=spectrum[(frequencies>=120)&(frequencies<=1800)][:,voiced].sum()
            high=spectrum[(frequencies>=3500)&(frequencies<=7500)][:,voiced].sum()
            result={**row,"file":str(file),"is_previous_reference":file.name==Path(reference["file"]).name,
                "f0_median_hz":round(float(np.median(f0[voiced])),1),
                "f0_p10_hz":round(float(np.quantile(f0[voiced],.1)),1),
                "f0_p90_hz":round(float(np.quantile(f0[voiced],.9)),1),
                "voiced_fraction":round(float(voiced.mean()),3),
                "periodicity_median":round(float(np.median(periodic[voiced])),3),
                "rms_voiced":round(float(np.median(rms[voiced])),4),
                "low_high_energy_db":round(float(10*np.log10((low+1e-10)/(high+1e-10))),2),
                "characters_per_second":round(len(row["text"])/row["seconds"],2)}
            done[str(file)]=result;journal.write(json.dumps(result,ensure_ascii=False)+"\n");journal.flush()
            if index%50==0:print(json.dumps({"measured":index,"total":len(targets)}),flush=True)
    rows=list(done.values())
    candidates=[r for r in rows if not r["is_previous_reference"] and r["voiced_fraction"]>=.35 and r["rms_voiced"]>=.015 and 3.5<=r["seconds"]<=7.5 and r["characters_per_second"]<=8.5]
    candidates.sort(key=lambda r:(r["f0_median_hz"],-r["low_high_energy_db"]))
    (output/"lower-register-candidates.json").write_text(json.dumps(candidates,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"total_measured":len(rows),"previous_reference":[{k:r[k] for k in ('file','text','f0_median_hz','voiced_fraction','low_high_energy_db')} for r in rows if r["is_previous_reference"]],"lower_candidates":[{k:r[k] for k in ('file','text','f0_median_hz','voiced_fraction','low_high_energy_db')} for r in candidates[:12]]},ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
