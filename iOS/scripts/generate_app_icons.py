"""Generate the iPhone icon assets from the approved Universal Guide TV artwork.

The asset catalog is built by CI to avoid committing generated binary icon files.
"""
import json
from pathlib import Path
from PIL import Image, ImageOps

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT.parent/"tvOS"/"Branding"/"UniversalGuideIcon.png"
ICONSET=ROOT/"Assets.xcassets"/"AppIcon.appiconset"
INFO={"author":"xcode","version":1}

def main():
    if not SOURCE.is_file():
        raise SystemExit(f"Missing approved Universal Guide artwork: {SOURCE}")
    source=Image.open(SOURCE).convert("RGBA")
    ICONSET.mkdir(parents=True,exist_ok=True)
    (ROOT/"Assets.xcassets"/"Contents.json").write_text(
        json.dumps({"info":INFO},indent=2)+"\n")
    specs=[("iphone","20x20",2),("iphone","20x20",3),
           ("iphone","29x29",2),("iphone","29x29",3),
           ("iphone","40x40",2),("iphone","40x40",3),
           ("iphone","60x60",2),("iphone","60x60",3),
           ("ios-marketing","1024x1024",1)]
    images=[]
    for idiom,size,scale in specs:
        dim=1024 if idiom=="ios-marketing" else int(size.split("x")[0])*scale
        background=Image.new("RGB",(dim,dim),(14,19,32))
        # The TV asset is landscape. Fitting it inside an iOS square
        # letterboxes its logo and makes the home-screen icon look tiny.
        # Crop to a centered square, preserving aspect ratio and filling
        # the iOS icon canvas edge-to-edge instead.
        resized=ImageOps.fit(source,(dim,dim),
                             method=Image.Resampling.LANCZOS,
                             centering=(0.5,0.5))
        background.paste(resized,(0,0),resized)
        filename=f"AppIcon-{dim}.png"
        # The 40/58/60/80/87/120/180/1024 sizes are all distinct for this catalog.
        background.save(ICONSET/filename,format="PNG",optimize=True)
        images.append({"idiom":idiom,"size":size,"scale":f"{scale}x","filename":filename})
    (ICONSET/"Contents.json").write_text(json.dumps({"images":images,"info":INFO},indent=2)+"\n")
    assert (ICONSET/"AppIcon-120.png").is_file()
    print("Generated iPhone app icon assets including 120x120 and 1024x1024 PNG.")

if __name__=="__main__":
    main()
