from PIL import Image, ImageDraw
import os
for size,name in [(32,'favicon-32x32.png'),(180,'apple-touch-icon.png')]:
    im = Image.new('RGBA',(size,size),(0,0,0,0))
    draw = ImageDraw.Draw(im)
    margin = size//8
    draw.rounded_rectangle([margin,margin,size-margin,size-margin], radius=size//5, fill=(79,70,229,255))
    cx,cy=size//2,size//2
    r=size//3
    draw.arc([cx-r,cy-r,cx+r,cy+r], start=200, end=340, fill=(255,255,255,255), width=max(2,size//16))
    draw.line([cx-r//2, cy, cx+r//2, cy], fill=(255,255,255,255), width=max(2,size//16))
    im.save(os.path.join('C:/Users/HP/Downloads/money_tracker/static/img',name))
print('ok')
