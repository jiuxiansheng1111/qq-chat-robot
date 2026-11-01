"""Build an original, editable chibi dragon-maid rig and facial morphs in Blender.

Run: blender --background --python scripts/build_dragon_maid_3d.py
Only writes artifacts/dragon-maid-3d. No external upload or model download.
"""
import bpy
import json
import math
from mathutils import Vector
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/dragon-maid-3d'
OUT.mkdir(parents=True, exist_ok=True)
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.engine = 'BLENDER_EEVEE_NEXT'
scene.render.resolution_x = 900
scene.render.resolution_y = 1000
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.fps = 30
scene.render.film_transparent = False
scene.world.color = (.16, .12, .22)
scene.view_settings.view_transform = 'AgX'
scene.render.image_settings.color_mode = 'RGBA'
scene.render.fps = 30
scene.frame_start = 1
scene.frame_end = 360

def material(name, rgba, metallic=0, roughness=.4):
    m = bpy.data.materials.new(name)
    m.diffuse_color = rgba
    m.use_nodes = True
    p = m.node_tree.nodes.get('Principled BSDF')
    p.inputs['Base Color'].default_value = rgba
    p.inputs['Roughness'].default_value = roughness
    p.inputs['Metallic'].default_value = metallic
    return m

skin = material('Porcelain skin', (1,.79,.72,1),roughness=.52)
white = material('Pearl white', (.94,.92,1,1),roughness=.3)
hair = material('Ivory silver hair', (.89,.86,.99,1),roughness=.27)
hair_light = material('Hair sheen', (1,.98,1,1),roughness=.25)
lavender = material('Lavender lining', (.47,.29,.70,1))
pale = material('Lilac accents', (.69,.53,.90,1))
dark = material('Plum outline', (.065,.025,.11,1),roughness=.6)
iris = material('Violet iris', (.35,.12,.64,1),roughness=.2)
iris_light = material('Iris luminous lower', (.70,.42,.91,1),roughness=.2)
shine = material('Eye highlights', (1,1,1,1),roughness=.13)
rose = material('Rosy blush', (.97,.40,.53,1),roughness=.7)
mouth_mat = material('Mouth inside', (.25,.045,.12,1))
tongue = material('Tongue', (.99,.33,.49,1))
stocking = material('Black silk stocking', (.045,.035,.075,1),roughness=.32)
silver = material('Silver trim', (.70,.66,.85,1),metallic=.65,roughness=.25)
tears_mat = material('Crystal tears', (.35,.74,1,1),metallic=.1,roughness=.12)

parts = []
def finish(obj, name, mat, bone='head'):
    obj.name = name
    obj.data.materials.append(mat)
    if obj.type == 'MESH':
        for f in obj.data.polygons: f.use_smooth = True
    obj['attach_bone'] = bone
    parts.append(obj)
    return obj

def ellipsoid(name, loc, scale, mat, bone='head', segments=28, rings=18):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=rings, location=loc)
    o=bpy.context.object
    o.scale=scale
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return finish(o,name,mat,bone)

def mesh(name,verts,faces,mat,bone='head'):
    d=bpy.data.meshes.new(name)
    d.from_pydata(verts,[],faces)
    d.update()
    o=bpy.data.objects.new(name,d)
    bpy.context.collection.objects.link(o)
    return finish(o,name,mat,bone)

def line(name,points,radius,mat,bone='head'):
    d=bpy.data.curves.new(name,'CURVE');d.dimensions='3D'
    d.resolution_u=16;d.bevel_depth=radius;d.bevel_resolution=3
    s=d.splines.new('BEZIER');s.bezier_points.add(len(points)-1)
    for p,co in zip(s.bezier_points,points):
        p.co=co;p.handle_left_type=p.handle_right_type='AUTO'
    o=bpy.data.objects.new(name,d);bpy.context.collection.objects.link(o)
    bpy.ops.object.select_all(action='DESELECT');o.select_set(True);bpy.context.view_layer.objects.active=o
    bpy.ops.object.convert(target='MESH')
    return finish(o,name,mat,bone)

def catmull(points,steps=8):
    v=[Vector(p) for p in points];p=[v[0]]+v+[v[-1]];out=[]
    for i in range(1,len(p)-2):
        a,b,c,d=p[i-1:i+3]
        for j in range(steps):
            t=j/steps
            out.append(.5*((2*b)+(-a+c)*t+(2*a-5*b+4*c-d)*t*t+(-a+3*b-3*c+d)*t*t*t))
    return out+[v[-1]]

def tube(name,points,radii,mat,bone='head',sides=12,depth=1):
    path=catmull(points)
    verts=[];faces=[]
    for i,p in enumerate(path):
        t=i/(len(path)-1);q=t*(len(radii)-1);k=min(int(q),len(radii)-2);u=q-k
        r=radii[k]*(1-u)+radii[k+1]*u
        tangent=(path[min(i+1,len(path)-1)]-path[max(0,i-1)]).normalized()
        normal=Vector((0,1,0));normal=(normal-tangent*normal.dot(tangent)).normalized()
        if normal.length<.1:normal=Vector((1,0,0))
        side=tangent.cross(normal).normalized()
        for j in range(sides):
            a=j*2*math.pi/sides
            verts.append(p+r*math.cos(a)*side+r*depth*math.sin(a)*normal)
    for i in range(len(path)-1):
        for j in range(sides):
            a=i*sides+j;b=i*sides+(j+1)%sides
            faces.append((a,b,b+sides,a+sides))
    faces.extend([tuple(range(sides-1,-1,-1)),tuple((len(path)-1)*sides+j for j in range(sides))])
    return mesh(name,verts,faces,mat,bone)

def ring(name,center,rx,ry,r,mat,bone='spine'):
    return line(name,[(center[0]+rx*math.cos(i*2*math.pi/48),center[1]+ry*math.sin(i*2*math.pi/48),center[2]) for i in range(49)],r,mat,bone)

# Body, outfit and limbs: separate editable meshes, all driven by named bones.
ellipsoid('Body', (0,0,1.28),(.275,.19,.37),skin,'spine')
ellipsoid('Neck',(0,0,1.70),(.11,.105,.16),skin,'neck')
ellipsoid('White dress bodice',(0,-.015,1.32),(.29,.205,.35),white,'spine')
ellipsoid('High collar',(0,-.01,1.62),(.13,.12,.09),white,'neck')
ring('Collar piping',(0,-.01,1.68),.123,.118,.013,pale,'neck')
for z in [1.52,1.40,1.28]:
    ellipsoid('Pearl clasp',(0,-.225,z),(.025,.018,.025),silver,'spine',16,10)
    line('Frog button', [(-.065,-.21,z),(0,-.235,z+.025),(.065,-.21,z)],.009,pale,'spine')
verts=[];faces=[];n=64
for k in range(13):
    t=k/12;z=1.16-.46*t;r=.25+.28*t**.75
    for j in range(n):
        a=2*math.pi*j/n;ripple=.016*math.sin(12*a)*t
        verts.append(((r+ripple)*math.cos(a),(.72*r+ripple)*math.sin(a),z+.018*math.cos(12*a)*t))
for k in range(12):
    for j in range(n):
        a=k*n+j;b=k*n+(j+1)%n;faces.append((a,b,b+n,a+n))
mesh('Flared white skirt',verts,faces,white,'hips')
ring('Waist ribbon',(0,0,1.15),.26,.19,.028,lavender,'hips')
ring('Skirt lavender border',(0,0,.73),.51,.37,.024,pale,'hips')
for i in range(30):
    a=2*math.pi*i/30
    ellipsoid('Lace hem',(.51*math.cos(a),.37*math.sin(a),.70),(.043,.035,.034),white,'hips',16,10)
for s in [-1,1]:
    b='L' if s>0 else 'R'
    ellipsoid('Shoulder '+b,(s*.26,0,1.52),(.13,.12,.14),skin,'upper_arm.'+b)
    tube('Arm '+b,[(s*.28,0,1.5),(s*.43,-.005,1.33),(s*.59,-.03,1.20)],[.095,.083,.065],skin,'upper_arm.'+b)
    tube('Sleeve '+b,[(s*.35,0,1.42),(s*.47,-.01,1.30),(s*.59,-.03,1.20)],[.12,.12,.17],white,'forearm.'+b,depth=.88)
    ellipsoid('Cuff '+b,(s*.60,-.03,1.18),(.17,.13,.055),pale,'forearm.'+b)
    ellipsoid('Hand '+b,(s*.64,-.04,1.09),(.083,.055,.105),skin,'hand.'+b)
    for f in range(4):
        ellipsoid('Finger '+b,(s*(.59+f*.028),-.052,1.015),(.017,.024,.052),skin,'hand.'+b,12,10)
    ellipsoid('Thumb '+b,(s*.72,-.075,1.09),(.035,.03,.06),skin,'hand.'+b,16,12)
    sm=stocking if s<0 else white
    tube('Stocking leg '+b,[(s*.17,0,.84),(s*.18,0,.49),(s*.19,-.01,.18)],[.09,.075,.055],sm,'thigh.'+b,sides=20)
    ring('Stocking top '+b,(s*.17,0,.78),.09,.091,.017,pale,'thigh.'+b)
    ellipsoid('Shoe '+b,(s*.19,-.07,.115),(.085,.15,.08),lavender,'foot.'+b)
    ellipsoid('Shoe pearl '+b,(s*.19,-.15,.16),(.025,.025,.024),silver,'foot.'+b,12,10)

# Large head and pointed dragon ears.
ellipsoid('Face',(0,-.015,2.20),(.585,.48,.58),skin)
for s in [-1,1]:
    ear=ellipsoid('Pointed ear',(s*.58,.01,2.22),(.18,.075,.09),skin)
    # Taper the outer half into a small elven point.
    for v in ear.data.vertices:
        if abs(v.co.x)>.60:v.co.z+=.35*(abs(v.co.x)-.60)
    ellipsoid('Ear inner',(s*.62,-.053,2.23),(.095,.016,.04),rose)

# Hair shell: cap is cut high above the eyes, long locks cover the rear and sides.
vs=[];fs=[];rows=22;cols=64
for i in range(rows+1):
    for j in range(cols):
        a=2*math.pi*j/cols;front=max(0,-math.sin(a))
        theta=(i/rows)*(1.78-.75*front**4)
        vs.append((.62*math.sin(theta)*math.cos(a),.035+.51*math.sin(theta)*math.sin(a),2.26+.60*math.cos(theta)))
for i in range(rows):
    for j in range(cols):
        a=i*cols+j;b=i*cols+(j+1)%cols;fs.append((a,b,b+cols,a+cols))
mesh('Hair crown',vs,fs,hair)
for i in range(9):
    a=.05+math.pi*i/8;x=.48*math.cos(a);y=.18+.25*math.sin(a)
    tube('Long back lock %02d'%i,[(x,y,2.50),(x*1.13,y+.04,2.04),(x*1.15,y+.04,1.48),(x*1.28,y-.06,1.05+(i%3)*.06),(x*1.16,y-.12,1.00+(i%3)*.06)], [.15,.15,.11,.01],hair,'hair.back',depth=.46)
for s in [-1,1]:
    tube('Side lock',[(s*.48,-.08,2.72),(s*.59,-.22,2.42),(s*.59,-.27,1.98),(s*.67,-.23,1.68),(s*.57,-.27,1.56)],[.14,.15,.10,.008],hair,'hair.'+('L' if s>0 else 'R'),depth=.42)
    tube('Side fringe',[(s*.37,-.30,2.71),(s*.48,-.42,2.47),(s*.51,-.43,2.24)],[.13,.11,.006],white,depth=.35)
bangs=[([(-.38,-.32,2.71),(-.37,-.46,2.58),(-.38,-.46,2.43)], [.17,.12,.006]),
       ([(-.12,-.38,2.83),(-.12,-.51,2.63),(.10,-.52,2.40)],[.22,.19,.006]),
       ([(.13,-.37,2.82),(.24,-.48,2.66),(.32,-.48,2.47)],[.19,.14,.006])]
for i,(p,rad) in enumerate(bangs):
    tube('Sculpted fringe %d'%i,p,rad,hair,depth=.40)
    line('Fringe highlight %d'%i,[(x-.035,y-.045,z+.01) for x,y,z in p[:-1]]+[tuple(Vector(p[-1])+Vector((-.015,-.015,.06)))],.009,hair_light)
line('Ahoge',[(0,.02,2.83),(.015,.02,3.10),(.24,.02,3.17),(.30,.02,3.08),(.16,.02,3.03)],.025,hair)

# White horns with a subtle pearl ridge, no green ornament.
for s in [-1,1]:
    pts=[(s*.39,.04,2.71),(s*.47,.055,2.94),(s*.46,.07,3.10),(s*.37,.05,3.22)]
    tube('White dragon horn',pts,[.14,.11,.066,.002],white,depth=.88)
    for j in range(5):
        z=2.83+j*.052;r=.12-j*.012
        line('Horn ridge',[(s*.46+r*math.cos(a),.05+r*.85*math.sin(a),z+.025*math.cos(a)) for a in [k*2*math.pi/24 for k in range(25)]],.009,hair_light)

# One GPT-like six-loop knot hairpin on a small lavender base.
cx,cy,cz=.49,-.36,2.66
ellipsoid('Single GPT hairpin base',(cx,cy,cz),(.14,.036,.14),lavender)
for j in range(6):
    a=2*math.pi*j/6;points=[]
    for k in range(25):
        t=2*math.pi*k/24;u=.048+.060*math.cos(t);v=.033*math.sin(t)
        points.append((cx+u*math.cos(a)-v*math.sin(a),cy-.043-.008*math.sin(2*t),cz+u*math.sin(a)+v*math.cos(a)))
    line('GPT knot loop',points,.013,white)
line('Hairpin tassel',[(cx+.06,cy,cz-.12),(cx+.07,cy,cz-.25),(cx+.04,cy,cz-.35)],.008,silver)
ellipsoid('Hairpin crystal',(cx+.04,cy,cz-.35),(.028,.022,.06),pale)

# Bat-like little wings, with curved scallops and pale wing spars.
for s in [-1,1]:
    b='wing.'+('L' if s>0 else 'R')
    root=Vector((s*.27,.28,1.46))
    outline=[(s*.40,.30,1.73),(s*.83,.31,2.03),(s*1.08,.29,1.64),
             (s*.90,.29,1.68),(s*.83,.28,1.44),(s*.69,.28,1.52),(s*.58,.28,1.30),(s*.43,.27,1.42)]
    wing=mesh('Lavender wing membrane',[root]+[Vector(p) for p in outline],[(0,i+1,(i+1)%len(outline)+1) for i in range(len(outline))],pale,b)
    mod=wing.modifiers.new('Soft membrane thickness','SOLIDIFY');mod.thickness=.018
    line('Wing outer rim',[tuple(root)]+outline+[tuple(root)],.018,white,b)
    for tip in [outline[2],outline[4],outline[6]]:line('Wing rib',[tuple(root),((root.x+tip[0])/2,.26,(root.z+tip[2])/2+.09),tip],.014,white,b)

tail_points=[(0,.26,.96),(.17,.46,.65),(.57,.46,.42),(.89,.32,.55),(1.04,.20,.91),(.99,.13,1.16)]
tail=tube('White dragon tail',tail_points,[.16,.15,.10,.045],white,'tail.01',sides=20)
for i in range(6):
    z=.57+i*.09;x=.90+.10*math.sin(i*.25)
    ellipsoid('Tail pearl scale',(x,.14,z),(.06,.025,.055),hair_light,'tail.03',14,10)
for j in [-1,0,1]:
    tube('Tail petal',[(.99,.13,1.10),(1.0+j*.08,.12,1.24),(1.0+j*.13,.12,1.39-abs(j)*.07)],[.07,.09,.002],pale,'tail.03',depth=.32)

# Facial morph targets. Every facial part shares names for simple runtime mixing.
EXPRESSIONS=['Blink','Smile','Happy','Sad','Cry','Angry','Pout','Shy','Surprised','Sleepy','Tsundere','A','E','I','O','U']
facials=[]
def morphs(obj, fn):
    obj.shape_key_add(name='Basis')
    base=[v.co.copy() for v in obj.data.vertices]
    for name in EXPRESSIONS:
        key=obj.shape_key_add(name=name)
        for v,co in zip(key.data,base):v.co=fn(co.copy(),name)
    facials.append(obj)
    return obj

def eye_deformer(cx,cz):
    def deform(v,name):
        factor={'Blink':.035,'Smile':.82,'Happy':.10,'Sad':.8,'Cry':.30,'Angry':.57,
                'Pout':.80,'Shy':.72,'Surprised':1.13,'Sleepy':.30,'Tsundere':.58}.get(name,1)
        dx=v.x-cx;v.z=cz+(v.z-cz)*factor
        if name=='Happy':v.z+=.048*(1-(dx/.2)**2)
        if name in ['Angry','Tsundere']:v.z+=.32*dx*(1 if cx>0 else -1)
        if name in ['Sad','Cry']:v.z-=.20*dx*(1 if cx>0 else -1)
        return v
    return deform

for s in [-1,1]:
    cx=s*.235;cz=2.23
    f=eye_deformer(cx,cz)
    for name,loc,sc,ma in [
        ('Eye outline',(cx,-.444,cz),(.194,.050,.221),dark),
        ('Eye white',(cx,-.461,cz),(.178,.046,.200),shine),
        ('Violet iris',(cx,-.505,cz-.018),(.122,.023,.166),iris),
        ('Iris gradient',(cx,-.522,cz-.074),(.095,.010,.088),iris_light),
        ('Pupil',(cx,-.533,cz-.005),(.047,.009,.098),dark),
        ('Eye glint',(cx-.040,-.545,cz+.062),(.040,.008,.047),shine),
        ('Eye small glint',(cx+.049,-.544,cz-.067),(.018,.006,.022),shine)]:
        morphs(ellipsoid(name,loc,sc,ma,segments=24,rings=16),f)
    lid=[(cx-.18,-.49,cz+.07),(cx-.11,-.505,cz+.17),(cx+.03,-.505,cz+.20),(cx+.15,-.49,cz+.13)]
    morphs(line('Upper eyelash',lid,.018,dark),f)
    morphs(line('Lash flick',[(cx+s*.13,-.49,cz+.13),(cx+s*.22,-.47,cz+.20)],.014,dark),f)
    brow=line('Expressive eyebrow',[(cx-.12,-.43,2.50),(cx,-.47,2.52),(cx+.12,-.43,2.50)],.017,lavender)
    def brow_fn(v,name,cx=cx,s=s):
        if name in ['Angry','Tsundere']:v.z+=s*(v.x-cx)*.65-.04
        if name in ['Sad','Cry']:v.z-=s*(v.x-cx)*.7
        if name=='Surprised':v.z+=.08
        if name in ['Smile','Happy','Shy']:v.z+=.025
        return v
    morphs(brow,brow_fn)
    blush=ellipsoid('Cheek blush',(s*.39,-.398,2.06),(.075,.010,.029),rose,segments=20,rings=12)
    def blush_fn(v,name,s=s):
        if name in ['Shy','Tsundere','Pout','Angry']:
            v.x=s*.39+(v.x-s*.39)*1.3;v.z=2.06+(v.z-2.06)*1.35
        return v
    morphs(blush,blush_fn)
    for i in range(3):line('Blush stroke',[(s*(.35+i*.027),-.414,2.079),(s*(.36+i*.027),-.416,2.045)],.003,white)
    tear=ellipsoid('Tear drop',(s*.34,-.51,2.02),(.042,.024,.095),tears_mat,segments=20,rings=16)
    # Basis is collapsed; Cry restores the actual droplet without transparency hacks.
    actual=[v.co.copy() for v in tear.data.vertices];center=Vector((s*.34,-.51,2.04))
    for v in tear.data.vertices:v.co=center+(v.co-center)*.001
    tear.shape_key_add(name='Basis')
    for name in EXPRESSIONS:
        key=tear.shape_key_add(name=name)
        if name=='Cry':
            for v,co in zip(key.data,actual):v.co=co
    facials.append(tear)
ellipsoid('Little nose',(0,-.491,2.115),(.021,.018,.023),skin,segments=16,rings=12)

mouth_specs={'Basis':(.075,.018,0,0), 'Smile':(.105,.035,.012,.015),'Happy':(.123,.080,.015,.015),
             'Sad':(.09,.021,-.01,-.04),'Cry':(.108,.060,-.01,-.04),'Angry':(.080,.035,-.005,-.02),
             'Pout':(.044,.044,0,0),'Shy':(.065,.019,.01,.015),'Surprised':(.055,.083,0,0),
             'Sleepy':(.052,.015,-.01,0),'Tsundere':(.061,.023,0,-.015),
             'A':(.082,.085,0,0),'E':(.108,.045,0,0),'I':(.119,.025,0,0),'O':(.049,.074,0,0),'U':(.033,.052,0,0)}
def mouth_coordinates(name):
    w,h,offset,arc=mouth_specs.get(name,mouth_specs['Basis'])
    y=-.487-(.025 if name=='Pout' else 0)
    vs=[(0,y,2.015+offset)]
    for i in range(48):
        a=2*math.pi*i/48;x=w*math.cos(a)
        vs.append((x,y+.18*x*x,2.015+offset+h*math.sin(a)+arc*(x/w)**2))
    return vs
mouth=mesh('Mouth phonemes',mouth_coordinates('Basis'),[(0,i+1,(i+1)%48+1) for i in range(48)],mouth_mat)
mouth.shape_key_add(name='Basis')
for name in EXPRESSIONS:
    key=mouth.shape_key_add(name=name)
    for v,co in zip(key.data,mouth_coordinates(name)):v.co=co
facials.append(mouth)

# Skeleton. Parts can be reshaped or replaced without rebuilding the hierarchy.
bpy.ops.object.select_all(action='DESELECT')
arm=bpy.data.armatures.new('DragonMaidRig')
rig=bpy.data.objects.new('DragonMaid_Rig',arm)
bpy.context.collection.objects.link(rig)
bpy.context.view_layer.objects.active=rig;rig.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
bones={}
def bone(name,head,tail,parent=None):
    b=arm.edit_bones.new(name);b.head=head;b.tail=tail
    if parent:b.parent=arm.edit_bones[parent]
    bones[name]=(Vector(head),Vector(tail))
bone('root',(0,0,0),(0,0,.25))
bone('hips',(0,0,.80),(0,0,1.12),'root')
bone('spine',(0,0,1.12),(0,0,1.58),'hips')
bone('neck',(0,0,1.58),(0,0,1.77),'spine')
bone('head',(0,0,1.77),(0,0,2.45),'neck')
for s in [-1,1]:
    b='L' if s>0 else 'R'
    bone('upper_arm.'+b,(s*.27,0,1.53),(s*.45,-.01,1.31),'spine')
    bone('forearm.'+b,(s*.45,-.01,1.31),(s*.61,-.03,1.15),'upper_arm.'+b)
    bone('hand.'+b,(s*.61,-.03,1.15),(s*.64,-.04,1.02),'forearm.'+b)
    bone('thigh.'+b,(s*.17,0,.84),(s*.18,0,.49),'hips')
    bone('shin.'+b,(s*.18,0,.49),(s*.19,-.01,.18),'thigh.'+b)
    bone('foot.'+b,(s*.19,-.01,.18),(s*.19,-.19,.10),'shin.'+b)
    bone('wing.'+b,(s*.27,.28,1.46),(s*.82,.30,1.72),'spine')
    bone('hair.'+b,(s*.48,0,2.65),(s*.59,-.2,1.7),'head')
bone('hair.back',(0,.35,2.5),(0,.4,1.15),'head')
bone('tail.01',tail_points[0],tail_points[2],'hips')
bone('tail.02',tail_points[2],tail_points[4],'tail.01')
bone('tail.03',tail_points[4],tail_points[5],'tail.02')
bpy.ops.object.mode_set(mode='OBJECT')
for o in parts:
    name=o['attach_bone']
    g=o.vertex_groups.new(name=name);g.add(list(range(len(o.data.vertices))),1,'REPLACE')
    if o.name.startswith('Stocking leg'):
        suffix=name.split('.')[1];g2=o.vertex_groups.new(name='shin.'+suffix)
        for v in o.data.vertices:
            w=max(0,min(1,(.58-v.co.z)/.17));g.add([v.index],1-w,'REPLACE');g2.add([v.index],w,'REPLACE')
    if o == tail:
        g2=o.vertex_groups.new(name='tail.02');g3=o.vertex_groups.new(name='tail.03')
        for v in o.data.vertices:
            w=max(0,min(1,(v.co.x-.35)/.4));w3=max(0,min(1,(v.co.z-.83)/.30))*w
            g.add([v.index],1-w,'REPLACE');g2.add([v.index],w-w3,'REPLACE');g3.add([v.index],w3,'REPLACE')
    mod=o.modifiers.new('Armature','ARMATURE');mod.object=rig
    o.parent=rig

# 12 second, 30 fps demonstration with smooth breathing and expressive reactions.
for p in rig.pose.bones:p.rotation_mode='XYZ'
for frame in range(1,362,3):
    t=(frame-1)/30
    rig.pose.bones['root'].location.z=.012*math.sin(t*2.2)
    rig.pose.bones['spine'].rotation_euler.y=.024*math.sin(t*1.4)
    rig.pose.bones['head'].rotation_euler=(.025*math.sin(t*1.7),.04*math.sin(t*1.2),.04*math.sin(t*.8))
    for s in ['L','R']:
        rig.pose.bones['hair.'+s].rotation_euler.y=.035*math.sin(t*2+(.4 if s=='L' else 0))
        rig.pose.bones['wing.'+s].rotation_euler.y=.075*math.sin(t*1.6)*(-1 if s=='L' else 1)
    for i in range(1,4):rig.pose.bones['tail.%02d'%i].rotation_euler.y=.10*math.sin(t*1.3-i*.4)
    wave=max(0,1-abs(t-2.1)/1.3)
    rig.pose.bones['upper_arm.R'].rotation_euler.y=-.9*wave
    rig.pose.bones['forearm.R'].rotation_euler.x=1.1*wave
    rig.pose.bones['hand.R'].rotation_euler.z=.24*math.sin(t*9)*wave
    if 9.3<t<11.5:
        f=math.sin((t-9.3)/2.2*math.pi)
        rig.pose.bones['head'].rotation_euler.z+=.18*f
        rig.pose.bones['head'].rotation_euler.x-=.10*f
    for p in rig.pose.bones:
        p.keyframe_insert('rotation_euler',frame=frame)
        if p.name=='root':p.keyframe_insert('location',frame=frame)
schedule=[(1,'Smile'),(48,'Happy'),(103,'Surprised'),(138,'Pout'),(177,'Angry'),(212,'Cry'),(252,'Shy'),(285,'Tsundere'),(329,'Smile'),(360,'Smile')]
for o in facials:
    for key in o.data.shape_keys.key_blocks:
        if key.name=='Basis':continue
        for frame,expression in schedule:
            key.value=1 if key.name==expression else 0;key.keyframe_insert('value',frame=frame)
        if key.name=='Blink':
            for f in [30,121,193,318]:
                for dt,v in [(-3,0),(0,1),(3,0)]:key.value=v;key.keyframe_insert('value',frame=f+dt)
rig['expression_names']=','.join(EXPRESSIONS)
rig['identity']='龙女仆 / Dragon Maid'
rig['model_version']='0.1 editable chibi'
rig['notes']='Original procedural model derived from user-approved character description; facial morphs are adjustable, not live camera tracking'

# Presentation stage and a front three-quarter camera.
stage=material('Studio floor',(.16,.105,.24,1),roughness=.65)
bpy.ops.mesh.primitive_plane_add(size=200, location=(0,0,-.02));floor=bpy.context.object;floor.name='StudioFloor';floor.data.materials.append(stage)
def aim(o,point):o.rotation_euler=(Vector(point)-o.location).to_track_quat('-Z','Y').to_euler()
def area(name,loc,power,color,size):
    d=bpy.data.lights.new(name,'AREA');d.energy=power;d.color=color;d.shape='DISK';d.size=size
    o=bpy.data.objects.new(name,d);bpy.context.collection.objects.link(o);o.location=loc;aim(o,(0,0,1.5))
area('Key',(-3,-4,6),700,(1,.88,.94),4)
area('Fill',(3,-2,3),500,(.77,.76,1),3)
area('Rim',(1,3,5),900,(.90,.78,1),3)
camdata=bpy.data.cameras.new('Portrait camera');cam=bpy.data.objects.new('Portrait camera',camdata)
bpy.context.collection.objects.link(cam);cam.location=(3.3,-8,3.5);aim(cam,(.08,0,1.65))
camdata.type='ORTHO';camdata.ortho_scale=3.85;scene.camera=cam
scene.frame_set(1)
bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'dragon-maid-chibi.blend'))
# Export only the model, its rig and its morph animation, not the studio.
bpy.ops.object.select_all(action='DESELECT')
rig.select_set(True)
for o in parts:o.select_set(True)
bpy.context.view_layer.objects.active=rig
bpy.ops.export_scene.gltf(filepath=str(OUT/'dragon-maid-chibi.glb'),export_format='GLB',use_selection=True,
    export_animations=True,export_animation_mode='SCENE',export_morph=True,export_skins=True,
    export_extras=True,export_frame_range=True,export_force_sampling=True,export_anim_scene_split_object=False)
manifest={'name':'龙女仆','version':'0.1','format':['blend','glb'],'bones':list(bones),'expressions':EXPRESSIONS,
          'preview_animation_seconds':12,'fps':30,'meshes':len(parts),
          'vertices':sum(len(o.data.vertices) for o in parts),'style':'white-lavender chibi',
          'limitations':['Not VRM; requires conversion for applications that only accept VRM','No live face tracking or microphone lip sync wired yet']}
(OUT/'model-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
for frame,name in [(1,'smile'),(148,'pout'),(220,'cry'),(291,'tsundere')]:
    scene.frame_set(frame);scene.render.filepath=str(OUT/(name+'.png'));bpy.ops.render.render(write_still=True)
print('DRAGON_MAID_MODEL_READY',flush=True)
