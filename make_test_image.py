"""生成一张模拟 FPS 游戏画面的测试图，用来验证模型读界面数字的能力。"""
from PIL import Image, ImageDraw

W, H = 960, 540
img = Image.new("RGB", (W, H), (58, 66, 52))
d = ImageDraw.Draw(img)

# 地面与天空
d.rectangle([0, 0, W, 240], fill=(96, 122, 140))
d.rectangle([0, 240, W, H], fill=(74, 82, 66))
# 远处建筑块
d.rectangle([120, 180, 260, 300], fill=(92, 88, 80))
d.rectangle([600, 150, 760, 310], fill=(84, 80, 74))
# 掩体
d.rectangle([380, 330, 520, 430], fill=(110, 104, 92))

# 准心
d.line([W // 2 - 14, H // 2, W // 2 - 4, H // 2], fill=(255, 255, 255), width=2)
d.line([W // 2 + 4, H // 2, W // 2 + 14, H // 2], fill=(255, 255, 255), width=2)
d.line([W // 2, H // 2 - 14, W // 2, H // 2 - 4], fill=(255, 255, 255), width=2)
d.line([W // 2, H // 2 + 4, W // 2, H // 2 + 14], fill=(255, 255, 255), width=2)

# 血量
d.rectangle([40, 460, 240, 500], fill=(30, 30, 30))
d.text((52, 474), "HP  30 / 100", fill=(230, 80, 80))
# 弹药
d.rectangle([720, 460, 920, 500], fill=(30, 30, 30))
d.text((732, 474), "AMMO  12 / 90", fill=(240, 240, 240))
# 计分板
d.rectangle([380, 20, 580, 56], fill=(30, 30, 30))
d.text((430, 32), "3  vs  5", fill=(255, 255, 255))
# 小地图
d.ellipse([40, 40, 200, 200], outline=(200, 200, 200), width=2)
d.rectangle([40, 40, 200, 200], fill=(20, 20, 20))
d.ellipse([40, 40, 200, 200], outline=(200, 200, 200), width=2)
d.text((108, 112), "N", fill=(255, 255, 255))

img.save("test.jpg", quality=88)
print("已生成 test.jpg", img.size)
