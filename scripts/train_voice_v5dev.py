"""Resume local V5Dev fine-tuning, preserving source audio and per-epoch exports."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import yaml


def write_json(path: Path, value) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def run_task(python, wrapper, gpt, entry, arguments, env, log):
    command = [str(python), str(wrapper), "--gpt-root", str(gpt), entry, *arguments]
    print(json.dumps({"stage":entry,"log":str(log)}), flush=True)
    with log.open("ab") as stream:
        result = subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f"{entry} exited {result.returncode}; see {log}")


def prepare_manifest(job: Path, role: str, sources: list[dict], output: Path) -> list[dict]:
    accepted = {}
    rejected = []
    for source in sources:
        if source["role"] != role:
            continue
        corpus = job / source.get("corpus_id", role)
        reviewed=corpus/"curated-asr.json"
        if source.get("requires_transcript_review") and not reviewed.exists():
            raise RuntimeError(f"Reviewed transcripts are not ready for {corpus.name}")
        transcript_file=reviewed if reviewed.exists() else corpus/"asr.json"
        for row in json.loads(transcript_file.read_text(encoding="utf-8")):
            allowed = row["language"] in source.get("allowed_languages", ["ja", "zh", "en"])
            if not row["accepted"] or not allowed or "bgm" in row["tags"]:
                rejected.append({**row,"training_decision":"ASR/language quality check"})
                continue
            if len(row["text"].strip("。、，！？.!? …")) < 2:
                rejected.append({**row,"training_decision":"Nonlexical or isolated syllable"})
                continue
            accepted.setdefault(row["pcm_sha256"], row)
    rows = list(accepted.values())
    if len(rows) < 10:
        raise RuntimeError(f"Only {len(rows)} labeled utterances for {role}")
    names = [Path(r["file"]).name for r in rows]
    if len(names) != len(set(names)):
        raise RuntimeError("Feature filenames collide across source corpora")
    content = "\n".join(f"{r['file']}|{role}|{r['language']}|{r['text']}" for r in rows)+"\n"
    manifest = output / "training.list"
    if manifest.exists() and manifest.read_text(encoding="utf-8") != content:
        raise RuntimeError("Training manifest changed; use a new experiment directory")
    manifest.write_text(content, encoding="utf-8")
    fingerprint = hashlib.sha256((content+"\n"+"\n".join(r["pcm_sha256"] for r in rows)).encode()).hexdigest()
    write_json(output / "data-audit.json", {"unique_utterances":len(rows),
               "seconds":round(sum(r["seconds"] for r in rows),2),
               "gpt_reliable_utterances":sum(r.get("gpt_reliable",True) for r in rows),
               "languages":{lang:sum(r["language"]==lang for r in rows) for lang in {r["language"] for r in rows}},
               "fingerprint":fingerprint,"rejected":rejected,"originals_preserved":True})
    return rows


def train_role(args, role, state):
    project, job, gpt = args.project, args.job, args.gpt_root
    output = job / "experiments" / role
    output.mkdir(parents=True, exist_ok=True)
    sources = json.loads((job / "sources.json").read_text(encoding="utf-8"))
    rows = prepare_manifest(job, role, sources, output)
    python = gpt / ".venv/Scripts/python.exe"
    wrapper = project / "scripts/run_gpt_sovits_task.py"
    env = dict(os.environ, OMP_NUM_THREADS="2", MKL_NUM_THREADS="2", PYTHONIOENCODING="utf-8",
               G2PW="0", version="v2", is_half="True", _CUDA_VISIBLE_DEVICES="0",
               inp_text=str(output/"training.list"), inp_wav_dir="", exp_name=f"{role}_v5dev_20261025",
               i_part="0", all_parts="1", opt_dir=str(output),
               bert_pretrained_dir=str(gpt/"GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large"),
               cnhubert_base_dir=str(gpt/"GPT_SoVITS/pretrained_models/chinese-hubert-base"),
               pretrained_s2G=str(gpt/"GPT_SoVITS/pretrained_models/gsv-v5-pretrained/s2Gv5dev.pth"),
               s2config_path=str(gpt/"GPT_SoVITS/configs/s2.json"), hz="25hz")
    env["PATH"] = str(project/"data/singing/runtime/bin")+os.pathsep+env.get("PATH","")
    status = state.setdefault(role, {})
    progress = job / "training-state.json"
    if not status.get("text_prepared"):
        run_task(python,wrapper,gpt,"GPT_SoVITS/prepare_datasets/1-get-text.py",[],env,output/"prepare-text.log")
        part = output / "2-name2text-0.txt"
        if len(part.read_text(encoding="utf-8").splitlines()) != len(rows):
            raise RuntimeError("Text frontend omitted utterances; inspect prepare-text.log")
        shutil.copy2(part, output/"2-name2text.txt")
        status["text_prepared"] = True
        write_json(progress,state)
    if not status.get("hubert_prepared"):
        run_task(python,wrapper,gpt,"GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py",[],env,output/"prepare-hubert.log")
        if len(list((output/"4-cnhubert").glob("*.pt"))) != len(rows) or len(list((output/"5-wav32k").glob("*.wav"))) != len(rows):
            raise RuntimeError("HuBERT preprocessing omitted utterances")
        status["hubert_prepared"] = True
        write_json(progress,state)
    if not status.get("semantic_prepared"):
        run_task(python,wrapper,gpt,"GPT_SoVITS/prepare_datasets/3-get-semantic.py",[],env,output/"prepare-semantic.log")
        text = (output/"6-name2semantic-0.tsv").read_text(encoding="utf-8")
        if len(text.splitlines()) != len(rows):
            raise RuntimeError("Semantic preprocessing omitted utterances")
        (output/"6-name2semantic.tsv").write_text("item_name\tsemantic_audio\n"+text,encoding="utf-8")
        status["semantic_prepared"] = True
        write_json(progress,state)
    reliable={Path(r["file"]).name for r in rows if r.get("gpt_reliable",True)}
    if len(reliable)<10:
        raise RuntimeError("Too few reliable text/audio pairs for GPT training")
    phonemes=(output/"2-name2text.txt").read_text(encoding="utf-8").splitlines()
    semantics=(output/"6-name2semantic.tsv").read_text(encoding="utf-8").splitlines()
    selected_phonemes=[line for line in phonemes if line.split("\t",1)[0] in reliable]
    selected_semantics=[line for line in semantics[1:] if line.split("\t",1)[0] in reliable]
    if len(selected_phonemes)!=len(reliable) or len(selected_semantics)!=len(reliable):
        raise RuntimeError("Reliable GPT feature IDs are inconsistent")
    (output/"gpt-name2text.txt").write_text("\n".join(selected_phonemes)+"\n",encoding="utf-8")
    (output/"gpt-name2semantic.tsv").write_text(semantics[0]+"\n"+"\n".join(selected_semantics)+"\n",encoding="utf-8")
    config = json.loads((gpt/"GPT_SoVITS/configs/s2.json").read_text(encoding="utf-8"))
    config["train"].update(epochs=args.epochs, batch_size=2, pretrained_s2G=env["pretrained_s2G"],
        pretrained_s2D="", if_save_latest=True, if_save_every_weights=True,save_every_epoch=1,
        gpu_numbers="0", grad_ckpt=True,lora_rank=32,log_interval=25)
    config["data"].update(exp_dir=str(output),num_workers=0)
    if role != "mako":
        config["train"]["batch_size"] = 4
        config["data"].update(num_workers=2, feature_cache=str(output/"s2-feature-cache"))
    config["model"]["version"]="v5dev"
    config.update(s2_ckpt_dir=str(output/"logs_s2"), save_weight_dir=str(output/"SoVITS_weights"),
                  name=f"{role}_v5dev",version="v5dev")
    (output/"SoVITS_weights").mkdir(exist_ok=True)
    s2 = output / "s2-v5dev.json"
    write_json(s2, config)
    if status.get("sovits_epochs",0) < args.epochs:
        run_task(python,wrapper,gpt,"GPT_SoVITS/s2_train_v3_lora.py",["--config",str(s2)],env,output/"train-sovits.log")
        exports=list((output/"SoVITS_weights").glob(f"*_e{args.epochs}_*.pth"))
        if len(exports)!=1 or exports[0].stat().st_size<100000:
            raise RuntimeError("Requested SoVITS epoch export is missing")
        status["sovits_epochs"]=args.epochs
        write_json(progress,state)
    config=yaml.safe_load((gpt/"GPT_SoVITS/configs/s1longer.yaml").read_text(encoding="utf-8"))
    base_config=json.loads((job/"pretrained-s1-config.json").read_text(encoding="utf-8"))
    config["model"]=base_config["model"]
    (output/"GPT_weights").mkdir(exist_ok=True)
    config["train"].update(epochs=args.epochs,batch_size=4,save_every_n_epoch=1,if_save_every_weights=True,
        if_save_latest=True,if_dpo=False,half_weights_save_dir=str(output/"GPT_weights"),
        exp_name=f"{role}_v5dev")
    config["data"]["num_workers"]=0
    config.update(pretrained_s1=str(gpt/"GPT_SoVITS/pretrained_models/s1v3.ckpt"),
                  train_semantic_path=str(output/"gpt-name2semantic.tsv"),train_phoneme_path=str(output/"gpt-name2text.txt"),
                  output_dir=str(output/"logs_s1_v5dev"))
    s1=output/"s1-v5dev.yaml"
    s1.write_text(yaml.safe_dump(config,allow_unicode=True),encoding="utf-8")
    if status.get("gpt_epochs",0)<args.epochs:
        run_task(python,wrapper,gpt,"GPT_SoVITS/s1_train.py",["--config_file",str(s1)],env,output/"train-gpt.log")
        export=output/"GPT_weights"/f"{role}_v5dev-e{args.epochs}.ckpt"
        if not export.is_file() or export.stat().st_size<1000000:
            raise RuntimeError("Requested GPT epoch export is missing")
        status["gpt_epochs"]=args.epochs
        write_json(progress,state)
    status.update(completed=True,finished_unix=time.time())
    state.pop("last_error",None)
    write_json(progress,state)
    print(json.dumps({"role":role,"stage":"completed","epochs":args.epochs}),flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--job",type=Path,required=True)
    parser.add_argument("--gpt-root",type=Path,required=True)
    parser.add_argument("--project",type=Path,required=True)
    parser.add_argument("--epochs",type=int,default=10)
    parser.add_argument("--roles",nargs="+",default=["mako","murasame","yoshino"])
    parser.add_argument("--restore-sidecar",action="store_true")
    args=parser.parse_args()
    path=args.job/"training-state.json"
    state=json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    try:
        for role in args.roles:
            train_role(args,role,state)
    except BaseException as error:
        state["last_error"]=str(error)
        write_json(path,state)
        raise
    finally:
        if args.restore_sidecar:
            print("Restoring one-click services.",flush=True)
            subprocess.run(["cmd.exe","/d","/c","call start-qq-chatrobot.bat <nul"],
                           cwd=args.project.resolve(),stdin=subprocess.DEVNULL,creationflags=0x08000000,check=False)


if __name__=="__main__":
    main()
