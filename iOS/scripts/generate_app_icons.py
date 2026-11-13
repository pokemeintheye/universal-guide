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
    source=Image.open(SOURCE).convert("RGB")
    # The approved 5:3 TV artwork has a baked-in rounded rectangle.
    # Isolate ONLY its original white/blue UG mark and arc; extending the
    # rounded rectangle into a square would create top/bottom seams.
    pixels=list(source.getdata())
    alpha=[]
    for red,green,blue in pixels:
        peak=max(red,green,blue)
        is_logo=(blue>red*1.45 and blue>green*1.05) or peak>155
        alpha.append(round(max(0,min(1,(peak-78)/85))*255) if is_logo else 0)
    mask=Image.new("L",source.size)
    mask.putdata(alpha)
    bounds=mask.getbbox()
    if bounds is None:
        raise SystemExit("Approved artwork has no visible UG logo")
    logo=source.crop(bounds)
    logo_mask=mask.crop(bounds)
    ICONSET.mkdir(parents=True,exist_ok=True)
    (ROOT/"Assets.xcassets"/"Contents.json").write_text(
        json.dumps({"info":INFO},indent=2)+"\\n")
    specs=[("iphone","20x20",2),("iphone","20x20",3),
           ("iphone","29x29",2),("iphone","29x29",3),
           ("iphone","40x40",2),("iphone","40x40",3),
           ("iphone","60x60",2),("iphone","60x60",3),
           ("ios-marketing","1024x1024",1)]
    images=[]
    for idiom,size,scale in specs:
        dim=1024 if idiom=="ios-marketing" else int(size.split("x")[0])*scale
        background=Image.new("RGB",(dim,dim),(29,32,40))
        factor=min(dim*0.89/logo.width,dim*0.75/logo.height)
        dimensions=(max(1,round(logo.width*factor)),max(1,round(logo.height*factor)))
        resized=logo.resize(dimensions,Image.Resampling.LANCZOS)
        resized_mask=logo_mask.resize(dimensions,Image.Resampling.LANCZOS)
        background.paste(resized,((dim-dimensions[0])//2,(dim-dimensions[1])//2),resized_mask)
        filename=f"AppIcon-{dim}.png"
        # Generate all required iPhone icon sizes from the same approved artwork.
        background.save(ICONSET/filename,format="PNG",optimize=True)
        images.append({"idiom":idiom,"size":size,"scale":f"{scale}x","filename":filename})
    (ICONSET/"Contents.json").write_text(json.dumps({"images":images,"info":INFO},indent=2)+"\n")
    assert (ICONSET/"AppIcon-120.png").is_file()
    print("Generated iPhone app icon assets including 120x120 and 1024x1024 PNG.")

if __name__=="__main__":
    main()
