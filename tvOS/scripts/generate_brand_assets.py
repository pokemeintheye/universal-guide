"""Generate tvOS icon layers from the approved black-and-purple artwork.
Source stored as a base64-encoded WebP text asset to retain it in GitHub.
"""
import argparse
import base64
import io
import json
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"Branding"/"ApprovedBlackIcon.webp.b64"
LEGACY=ROOT/"Branding"/"UniversalGuideIcon.png"
CATALOG=ROOT/"Assets.xcassets"
BRAND=CATALOG/"AppIcon.brandassets"
INFO={"author":"xcode","version":1}

def write(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,indent=2)+"\n",encoding="utf-8")

def imageset(path,images):
    manifest=[]
    for scale,image,stem in images:
        path.mkdir(parents=True,exist_ok=True)
        filename=stem+"@"+str(scale)+"x.png"
        image.save(path/filename,optimize=True)
        manifest.append({"idiom":"tv","filename":filename,"scale":str(scale)+"x"})
    write(path/"Contents.json",{"images":manifest,"info":INFO})

def iconstack(name,image,size,scales):
    # The approved source includes baked-in padding around its rounded border.
    # tvOS adds another parallax/rounded-mask inset. Trim only that outer
    # padding, then fill the full native 5:3 icon canvas. Keep the artwork,
    # lettering and black-purple palette unchanged.
    w,h=image.size
    image=image.crop((round(w*0.055),round(h*0.065),
                      round(w*0.945),round(h*0.935))).resize(
                          (w,h),Image.Resampling.LANCZOS)
    stack=BRAND/(name+".imagestack")
    write(stack/"Contents.json",{
        "layers":[{"filename":"Front.imagestacklayer"},{"filename":"Back.imagestacklayer"}],
        "info":INFO})
    for layername in ("Front","Back"):
        layer=stack/(layername+".imagestacklayer")
        write(layer/"Contents.json",{"info":INFO})
        items=[]
        for scale in scales:
            dimensions=(size[0]*scale,size[1]*scale)
            if layername=="Back":
                frame=image.resize(dimensions,Image.Resampling.LANCZOS)
            else:
                # Transparent parallax layer preserves the approved composite exactly.
                frame=Image.new("RGBA",dimensions,(0,0,0,0))
            items.append((scale,frame,layername.lower()))
        imageset(layer/"Content.imageset",items)

def shelf(image,size):
    width,height=size
    frame=Image.new("RGB",size,(5,5,10))
    # Fit approved composition on a wide TV shelf; never crop its lettering.
    scale=min(width/image.width,height/image.height)
    dims=(round(image.width*scale),round(image.height*scale))
    scaled=image.resize(dims,Image.Resampling.LANCZOS)
    frame.paste(scaled,((width-dims[0])//2,(height-dims[1])//2))
    return frame

def generate():
    if SOURCE.is_file():
        # Only decode the verified, checked-in approved artwork. No network
        # dependency or GitHub runner secret needed for App Store builds.
        encoded=SOURCE.read_text(encoding="ascii").strip()
        source=Image.open(io.BytesIO(base64.b64decode(encoded,validate=True))).convert("RGB")
    else:
        source=Image.open(LEGACY).convert("RGB")
    if source.size!=(1280,768):
        raise ValueError("Approved tvOS icon source must be exactly 1280x768")
    write(CATALOG/"Contents.json",{"info":INFO})
    iconstack("App Icon",source,(400,240),(1,2))
    iconstack("App Icon - App Store",source,(1280,768),(1,))
    imageset(BRAND/"Top Shelf Image.imageset",[
        (1,shelf(source,(1920,720)),"topshelf"),
        (2,shelf(source,(3840,1440)),"topshelf")])
    imageset(BRAND/"Top Shelf Image Wide.imageset",[
        (1,shelf(source,(2320,720)),"topshelfwide"),
        (2,shelf(source,(4640,1440)),"topshelfwide")])
    write(BRAND/"Contents.json",{
        "assets":[
            {"size":"1280x768","idiom":"tv","filename":"App Icon - App Store.imagestack","role":"primary-app-icon"},
            {"size":"400x240","idiom":"tv","filename":"App Icon.imagestack","role":"primary-app-icon"},
            {"size":"1920x720","idiom":"tv","filename":"Top Shelf Image.imageset","role":"top-shelf-image"},
            {"size":"2320x720","idiom":"tv","filename":"Top Shelf Image Wide.imageset","role":"top-shelf-image-wide"}],
        "info":INFO})
    print("Generated tvOS icon and top shelf assets.")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--optional",action="store_true")
    args=p.parse_args()
    if SOURCE.is_file() or LEGACY.is_file():
        generate()
    elif args.optional:
        print("Icon not uploaded yet; continuing unsigned simulator build.")
    else:
        p.error("Missing Apple TV brand source (ApprovedBlackIcon.webp.b64)")
