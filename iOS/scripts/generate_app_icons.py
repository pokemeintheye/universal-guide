"""Generate the iPhone icon assets from the approved Universal Guide TV artwork.

The asset catalog is built by CI to avoid committing generated binary icon files.
"""
import json
from pathlib import Path
from PIL import Image, ImageDraw

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
        # Use the current approved TV artwork as a full-width, square iPhone icon.
        # Keep the complete lettering visible instead of cropping the 5:3 mark.
        background=Image.new("RGB",(dim,dim),(14,19,32))
        factor=min(dim/source.width,dim/source.height)
        resized=source.resize((max(1,round(source.width*factor)),
                               max(1,round(source.height*factor))),
                              Image.Resampling.LANCZOS)
        background.paste(resized.convert("RGB"),
                         ((dim-resized.width)//2,(dim-resized.height)//2))
        filename=f"AppIcon-{dim}.png"
        # Generate all required iPhone icon sizes from the same approved artwork.
        background.save(ICONSET/filename,format="PNG",optimize=True)
        images.append({"idiom":idiom,"size":size,"scale":f"{scale}x","filename":filename})
    (ICONSET/"Contents.json").write_text(json.dumps({"images":images,"info":INFO},indent=2)+"\n")
    assert (ICONSET/"AppIcon-120.png").is_file()
    print("Generated iPhone app icon assets including 120x120 and 1024x1024 PNG.")

if __name__=="__main__":
    main()
