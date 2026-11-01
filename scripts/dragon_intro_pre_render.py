import json
import dragon_intro_render as r
scenes=json.loads((r.WORK/'story.json').read_text(encoding='utf-8'))
manifest=json.loads((r.WORK/'voice-manifest.json').read_text(encoding='utf-8'))
folder=r.WORK/'clips';folder.mkdir(exist_ok=True)
cachefile=r.WORK/'clips-manifest.json'
cache=json.loads(cachefile.read_text(encoding='utf-8')) if cachefile.exists() else {}
for i in range(17):
 wav=r.WORK/'voice'/manifest[str(i)]['file'];target=folder/f'scene-{i:02}.mp4';key=r.clip_key(scenes[i],wav)
 if not target.exists() or cache.get(str(i))!=key:
  r.render_scene(i,scenes[i],wav,target);cache[str(i)]=key;cachefile.write_text(json.dumps(cache,ensure_ascii=False,indent=2),encoding='utf-8')
 print('visual ready',i+1,flush=True)
