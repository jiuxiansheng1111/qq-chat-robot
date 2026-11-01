"""Synthesize review-only samples from trained epochs without editing bot routes."""
import argparse
import gc
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import yaml


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--job",type=Path,required=True)
    parser.add_argument("--gpt-root",type=Path,required=True)
    parser.add_argument("--role",required=True)
    parser.add_argument("--epochs",nargs="+",type=int,default=[3,6,10])
    parser.add_argument("--gpt-epoch",type=int)
    parser.add_argument("--seed",type=int,default=20261025)
    parser.add_argument("--reference-json",type=Path)
    args=parser.parse_args()
    temporary=Path(os.environ.get("PUBLIC","C:/Users/Public"))/"GPTSoVITS_temp"
    temporary.mkdir(exist_ok=True)
    os.environ["TEMP"]=os.environ["TMP"]=str(temporary)
    import tempfile
    tempfile.tempdir=str(temporary)
    job=args.job.absolute();gpt=args.gpt_root.absolute()
    experiment=job/"experiments"/args.role
    output=job/"previews"/args.role
    output.mkdir(parents=True,exist_ok=True)
    import numpy as np
    import soundfile as sf
    import torch
    torch.set_num_threads(2)
    if args.reference_json:
        reference=json.loads(args.reference_json.read_text(encoding="utf-8"))
    else:
        candidates=[]
        for line in (experiment/"training.list").read_text(encoding="utf-8").splitlines():
            file,_role,language,text=line.split("|",3)
            duration=sf.info(file).duration
            if 3.0<=duration<=8.0 and len(text)>=8 and not re.search("性|裸|エッチ|おっぱい|乳|尻|キス|脱|下着|服|風呂",text):
                score=abs(duration-4.5)+abs(len(text)/duration-5)*0.2
                candidates.append((score,{"file":file,"language":language,"text":text}))
        if not candidates:raise RuntimeError("No suitable clean reference utterance")
        reference=min(candidates,key=lambda item:item[0])[1]
        (output/"reference.json").write_text(json.dumps(reference,ensure_ascii=False,indent=2)+'\n',encoding="utf-8")
    reference["file"]=str(Path(reference["file"]).resolve())
    (output/"reference.json").write_text(json.dumps(reference,ensure_ascii=False,indent=2)+'\n',encoding="utf-8")
    os.chdir(gpt)
    sys.path[:0]=[str(gpt),str(gpt/"GPT_SoVITS")]
    from tools.portable_runtime import activate
    activate(change_cwd=False)
    import pyopenjtalk
    dictionary=Path(pyopenjtalk.OPEN_JTALK_DICT_DIR.decode("utf-8"))
    if dictionary.is_relative_to(gpt.resolve()):
        pyopenjtalk.OPEN_JTALK_DICT_DIR=str(gpt/dictionary.relative_to(gpt.resolve())).encode("utf-8")
    from TTS_infer_pack.TTS import TTS, TTS_Config
    base=yaml.safe_load((gpt/"GPT_SoVITS/configs/tts_infer_v5dev.yaml").read_text(encoding="utf-8"))
    # A separate configuration file prevents model selection from changing production settings.
    base["v5dev"]["vits_weights_path"]=str(gpt/"GPT_SoVITS/pretrained_models/gsv-v5-pretrained/s2Gv5dev.pth")
    texts={"zh":"晚上好！今天过得怎么样？太好了，我们一起慢慢聊吧。别着急，我会认真听你说。",
           "en":"Good evening! How was your day? That's wonderful. Take your time. I'm listening."}
    manifest=output/"manifest.json"
    results=json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else []
    for epoch in args.epochs:
        semantic_epoch=epoch if args.gpt_epoch is None else args.gpt_epoch
        suffix="" if semantic_epoch==epoch else f"-gpt{semantic_epoch}"
        if args.seed!=20261025:
            suffix+=f"-seed{args.seed}"
        def already_rendered(language):
            expected=output/f"{args.role}-e{epoch}{suffix}-{language}.wav"
            return expected.is_file() and any(
                r['file']==str(expected.resolve())
                and r.get('seed',20261025)==args.seed
                and r.get('reference')==reference for r in results)
        if all(already_rendered(lang) for lang in texts):
            continue
        sovits=list((experiment/"SoVITS_weights").glob(f"*_e{epoch}_*.pth"))
        gpt_weights=(gpt/"GPT_SoVITS/pretrained_models/s1v3.ckpt") if semantic_epoch==0 else experiment/"GPT_weights"/f"{args.role}_v5dev-e{semantic_epoch}.ckpt"
        if len(sovits)!=1 or not gpt_weights.is_file():raise RuntimeError(f"Missing trained epoch {epoch}")
        base["custom"].update(version="v5dev",device="cuda:0",is_half=True,
            vits_weights_path=str(sovits[0]),t2s_weights_path=str(gpt_weights))
        config_path=output/f"private-inference-e{epoch}{suffix}.yaml"
        config_path.write_text(yaml.safe_dump(base,allow_unicode=True),encoding="utf-8")
        pipeline=TTS(TTS_Config(str(config_path)))
        for language,text in texts.items():
            path=output/f"{args.role}-e{epoch}{suffix}-{language}.wav"
            if already_rendered(language):continue
            payload={"text":text,"text_lang":language,"ref_audio_path":reference["file"],
                "prompt_lang":reference["language"],"prompt_text":reference["text"],
                "top_k":15,"top_p":1.0,"temperature":0.9,"seed":args.seed,
                "text_split_method":"cut5","batch_size":1,"parallel_infer":False,
                "split_bucket":False,"use_cuda_graph":False,"speed_factor":1.0,
                "sample_steps":32,"streaming_mode":False}
            pieces=[];rate=None
            for sr,audio in pipeline.run(payload):
                rate=sr;pieces.append(audio)
            if not pieces:raise RuntimeError("Inference returned no audio")
            audio=np.concatenate(pieces)
            assert np.isfinite(audio).all() and np.max(np.abs(audio.astype("float64")))>0
            sf.write(path,audio,rate,subtype="PCM_16")
            result={"role":args.role,"epoch":epoch,"gpt_epochs":semantic_epoch,"sovits_epochs":epoch,"seed":args.seed,
                "language":language,"text":text,"file":str(path.resolve()),
                "seconds":round(len(audio)/rate,2),"reference":reference,
                "gpt_weights":str(gpt_weights.resolve()),"sovits_weights":str(sovits[0].resolve()),
                "sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"production_deployed":False}
            results.append(result)
            manifest.write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding="utf-8")
            print(json.dumps({k:result[k] for k in ['role','epoch','language','seconds','file']}),flush=True)
        del pipeline
        gc.collect();torch.cuda.empty_cache()


if __name__=="__main__":
    main()
