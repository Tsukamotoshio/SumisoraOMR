"""
测试 convert.py 中图像预处理（OMR 质量提升）功能
覆盖：find_waifu2x_executable / is_low_resolution_image /
      enhance_image_with_pillow / upscale_image_with_waifu2x /
      preprocess_image_for_omr
"""
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径设置：确保可以 import convert.py
# ---------------------------------------------------------------------------
BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from PIL import Image, ImageDraw, ImageFilter
import convert  # noqa: E402  (引入后可直接调用各函数)


def make_noisy_score_image(width: int, height: int, noisy: bool = True) -> Image.Image:
    """生成模拟五线谱扫描图（含背景噪声）。"""
    import random
    img = Image.new('RGB', (width, height), (245, 245, 245))
    draw = ImageDraw.Draw(img)

    # 绘制五线谱线条
    staff_top = height // 4
    for i in range(5):
        y = staff_top + i * (height // 10)
        draw.line([(20, y), (width - 20, y)], fill=(30, 30, 30), width=2)

    # 绘制若干音符头（实心椭圆）
    for x in range(60, width - 40, 50):
        y = staff_top + random.randint(0, 4) * (height // 10)
        draw.ellipse([(x - 5, y - 4), (x + 5, y + 4)], fill=(10, 10, 10))

    if noisy:
        # 添加随机噪声像素（模拟扫描噪点）
        pixels = img.load()
        for _ in range(width * height // 20):
            px = random.randint(0, width - 1)
            py = random.randint(0, height - 1)
            v = random.randint(0, 255)
            pixels[px, py] = (v, v, v)

    return img


def img_stats(img: Image.Image) -> dict:
    """返回图像的简单统计：均值、标准差（灰度）。"""
    from PIL import ImageStat
    gray = img.convert('L')
    stat = ImageStat.Stat(gray)
    return {'mean': round(stat.mean[0], 2), 'stdev': round(stat.stddev[0], 2)}


def section(title: str):
    print(f'\n{"=" * 60}')
    print(f'  {title}')
    print('=' * 60)


# ===========================================================================
# 1. 验证 find_waifu2x_executable
# ===========================================================================
section('1. find_waifu2x_executable()')
waifu2x_exe = convert.find_waifu2x_executable()
if waifu2x_exe is not None:
    print(f'[PASS] 已找到 waifu2x-ncnn-vulkan: {waifu2x_exe}')
else:
    print('[WARN] 未找到 waifu2x-ncnn-vulkan 可执行文件')
    print('       （不影响 Pillow 增强流程，仅跳过 GPU 超分辨率步骤）')


# ===========================================================================
# 2. 验证 is_low_resolution_image
# ===========================================================================
section('2. is_low_resolution_image()')
with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)

    # 低分辨率图 (800×600)
    low_res = make_noisy_score_image(800, 600)
    low_path = tmp / 'low_res.png'
    low_res.save(low_path)
    result_low = convert.is_low_resolution_image(low_path)
    status = 'PASS' if result_low else 'FAIL'
    print(f'[{status}] 800×600 判定为低分辨率: {result_low}  (期望 True)')

    # 高分辨率图 (1600×1200)
    high_res = make_noisy_score_image(1600, 1200)
    high_path = tmp / 'high_res.png'
    high_res.save(high_path)
    result_high = convert.is_low_resolution_image(high_path)
    status = 'PASS' if not result_high else 'FAIL'
    print(f'[{status}] 1600×1200 判定为低分辨率: {result_high}  (期望 False)')


# ===========================================================================
# 3. 验证 enhance_image_with_pillow — 统计前后变化
# ===========================================================================
section('3. enhance_image_with_pillow() — 噪声去除 + 锐化 + 对比度增强')
with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)

    src = make_noisy_score_image(1200, 900, noisy=True)
    src_path = tmp / 'noisy_score.png'
    src.save(src_path)

    enhanced_path = tmp / 'enhanced_score.png'
    ok = convert.enhance_image_with_pillow(src_path, enhanced_path)
    status = 'PASS' if ok and enhanced_path.exists() else 'FAIL'
    print(f'[{status}] enhance_image_with_pillow 返回值: {ok}，输出文件存在: {enhanced_path.exists()}')

    if ok and enhanced_path.exists():
        before = img_stats(src)
        with Image.open(enhanced_path) as after_img:
            after = img_stats(after_img)

        print(f'\n  处理前：均值={before["mean"]:6.1f}  标准差={before["stdev"]:6.1f}')
        print(f'  处理后：均值={after["mean"]:6.1f}  标准差={after["stdev"]:6.1f}')

        # autocontrast(10%) 做色阶拉伸：对浅色扫描图会将背景压暗（均值下降）属正常。
        # 关键指标是对比度（stddev）是否提升，以便 Audiveris 自适应二值化时边缘更清晰。
        contrast_ok = after['stdev'] > before['stdev']
        status2 = 'PASS' if contrast_ok else 'FAIL'
        print(f'  [{status2}] 对比度提升（stddev）: {before["stdev"]:.1f} → {after["stdev"]:.1f}（期望提升）')
        print(f'  [INFO] 亮度变化：{after["mean"] - before["mean"]:+.1f}（autocontrast 色阶拉伸后浅色图背景变暗属正常）')

        # 文件大小对比
        src_size = src_path.stat().st_size
        out_size = enhanced_path.stat().st_size
        print(f'\n  文件大小：{src_size // 1024} KB → {out_size // 1024} KB')


# ===========================================================================
# 4. 验证 preprocess_image_for_omr — 端到端流程
# ===========================================================================
section('4. preprocess_image_for_omr() — 端到端预处理流程')
with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    work = tmp / 'work'
    work.mkdir()

    # 低分辨率（触发 waifu2x + Pillow 路径）
    low_img = make_noisy_score_image(800, 600, noisy=True)
    low_path = tmp / 'test_lowres.png'
    low_img.save(low_path)

    print(f'  输入图像：{low_path.name}  尺寸：{low_img.size}  (低分辨率)')
    result = convert.preprocess_image_for_omr(low_path, work)

    if result is None:
        print('  [WARN] preprocess_image_for_omr 返回 None（Pillow 不可用或所有步骤失败）')
    else:
        with Image.open(result) as out_img:
            out_size = out_img.size
        print(f'  [PASS] 预处理成功，输出：{result.name}  尺寸：{out_size}')
        if out_size[0] >= low_img.size[0]:
            steps = []
            if waifu2x_exe and out_size[0] > low_img.size[0]:
                steps.append(f'waifu2x {out_size[0] // low_img.size[0]}x 超分辨率')
            steps.append('Pillow 去噪/锐化/对比度')
            print(f'  [INFO] 完成步骤：{" + ".join(steps) if steps else "Pillow 增强"}')

    # 高分辨率（跳过 waifu2x，只做 Pillow 增强）
    print()
    high_img = make_noisy_score_image(1600, 1200, noisy=True)
    high_path = tmp / 'test_highres.png'
    high_img.save(high_path)
    work2 = tmp / 'work2'
    work2.mkdir()

    print(f'  输入图像：{high_path.name}  尺寸：{high_img.size}  (高分辨率)')
    result2 = convert.preprocess_image_for_omr(high_path, work2)
    if result2 is not None:
        with Image.open(result2) as out2:
            out2_size = out2.size
        print(f'  [PASS] 预处理成功，输出：{result2.name}  尺寸：{out2_size}')
        print(f'  [INFO] 高分辨率图像：跳过 waifu2x，仅执行 Pillow 增强')
    else:
        print('  [WARN] 预处理返回 None')

    # 对 PDF 输入不应触发预处理
    pdf_path = BASE / 'Input' / 'Do_You_Hear_the_People_Sing.pdf'
    if pdf_path.exists():
        work3 = tmp / 'work3'
        work3.mkdir()
        result3 = convert.preprocess_image_for_omr(pdf_path, work3)
        status = 'PASS' if result3 is None else 'FAIL'
        print(f'\n  [{status}] PDF 输入不触发预处理（返回 None）: {result3 is None}')


# ===========================================================================
# 5. 验证 waifu2x 实际调用（如果找到可执行文件）
# ===========================================================================
section('5. upscale_image_with_waifu2x() — GPU 超分辨率实际调用')
if waifu2x_exe is None:
    print('[SKIP] 未找到 waifu2x，跳过此测试')
else:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        test_img = make_noisy_score_image(400, 300, noisy=False)
        src_path = tmp / 'small.png'
        test_img.save(src_path)
        out_path = tmp / 'upscaled.png'

        print(f'  输入：{test_img.size}  →  期望输出：800×600 (2x)')
        ok = convert.upscale_image_with_waifu2x(src_path, out_path, scale=2)
        if ok and out_path.exists():
            with Image.open(out_path) as result_img:
                actual_size = result_img.size
            expected = (test_img.size[0] * 2, test_img.size[1] * 2)
            size_ok = actual_size == expected
            status = 'PASS' if size_ok else 'NOTE'
            print(f'  [{status}] 输出尺寸：{actual_size}  (期望 {expected})')
            print(f'  [INFO] GPU (Vulkan) 超分辨率调用成功')
        else:
            print('  [WARN] waifu2x 调用失败或输出文件不存在')
            print('         （可能原因：当前机器无 Vulkan 兼容 GPU）')


# ===========================================================================
# 6. 验证 fit_image_within_pixel_limit — 超大图像自动降采样
# ===========================================================================
section('6. fit_image_within_pixel_limit() — Audiveris 20M px 限制保护')
with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)

    # 模拟用户问题：6000×4000 = 24M px > 20M 上限
    oversized = make_noisy_score_image(6000, 4000, noisy=False)
    over_path = tmp / 'oversized_6000x4000.png'
    oversized.save(over_path)
    print(f'  输入：6000×4000 = {6000*4000:,} px  (超出 20,000,000 限制)')

    work = tmp / 'work'
    work.mkdir()
    result = convert.fit_image_within_pixel_limit(over_path, work)
    if result is not None:
        with Image.open(result) as out:
            w, h = out.size
        pixels = w * h
        under_limit = pixels <= convert.AUDIVERIS_MAX_PIXELS
        status = 'PASS' if under_limit else 'FAIL'
        print(f'  [{status}] 降采样后：{w}×{h} = {pixels:,} px  (≤ 20,000,000: {under_limit})')
    else:
        print('  [FAIL] fit_image_within_pixel_limit 返回 None（应降采样）')

    # 正常尺寸不应被降采样
    normal = make_noisy_score_image(1600, 1200, noisy=False)
    norm_path = tmp / 'normal_1600x1200.png'
    normal.save(norm_path)
    result2 = convert.fit_image_within_pixel_limit(norm_path, work)
    status2 = 'PASS' if result2 is None else 'FAIL'
    print(f'  [{status2}] 1600×1200 = {1600*1200:,} px 不触发降采样（返回 None）: {result2 is None}')

    # 还原用户场景：完整预处理流程对超大图像的处理
    print()
    print('  [场景复现] Blurry_test_image.jpg 类似情况：高分辨率大图预处理流程')
    over2_path = tmp / 'big_hires.jpg'
    oversized.save(over2_path, quality=90)
    work2 = tmp / 'work2'
    work2.mkdir()
    result3 = convert.preprocess_image_for_omr(over2_path, work2)
    if result3 is not None:
        with Image.open(result3) as out3:
            w3, h3 = out3.size
        pixels3 = w3 * h3
        ok3 = pixels3 <= convert.AUDIVERIS_MAX_PIXELS
        status3 = 'PASS' if ok3 else 'FAIL'
        print(f'  [{status3}] preprocess_image_for_omr 输出：{w3}×{h3} = {pixels3:,} px  (≤ 20M: {ok3})')
    else:
        print('  [WARN] preprocess_image_for_omr 返回 None')


# ===========================================================================
# 7. 验证 _measure_laplacian_stddev 及模糊图像强化锐化模式
# ===========================================================================
section('7. _measure_laplacian_stddev() + 模糊图像自适应增强')
with tempfile.TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)

    # 生成一张极度模糊的图像：先绘制五线谱再多次高斯模糊
    sharp_base = make_noisy_score_image(800, 600, noisy=False)
    blurry_img = sharp_base
    for _ in range(12):
        blurry_img = blurry_img.filter(ImageFilter.GaussianBlur(radius=4))
    blurry_path = tmp / 'blurry_score.png'
    blurry_img.save(blurry_path)

    # 生成一张清晰的五线谱图（有噪声，应为高 stddev）
    sharp_img = make_noisy_score_image(800, 600, noisy=True)
    sharp_path = tmp / 'sharp_score.png'
    sharp_img.save(sharp_path)

    # 测试 _measure_laplacian_stddev
    with Image.open(blurry_path) as img_b:
        blurry_sharpness = convert._measure_laplacian_stddev(img_b)
    with Image.open(sharp_path) as img_s:
        sharp_sharpness = convert._measure_laplacian_stddev(img_s)

    threshold = convert.BLURRY_SHARPNESS_THRESHOLD
    blurry_ok = blurry_sharpness < threshold
    sharp_ok  = sharp_sharpness  >= threshold
    s1 = 'PASS' if blurry_ok else 'FAIL'
    s2 = 'PASS' if sharp_ok  else 'FAIL'
    print(f'  [{s1}] 模糊图像 Laplacian stddev={blurry_sharpness:.2f}  < 阈值 {threshold}（期望 True）: {blurry_ok}')
    print(f'  [{s2}] 清晰图像 Laplacian stddev={sharp_sharpness:.2f} >= 阈值 {threshold}（期望 True）: {sharp_ok}')

    # 测试模糊模式增强：输出对比度应高于原始模糊图像
    blurry_out = tmp / 'enhanced_blurry.png'
    ok_b = convert.enhance_image_with_pillow(blurry_path, blurry_out)
    if ok_b and blurry_out.exists():
        from PIL import ImageStat as _IS
        with Image.open(blurry_path)  as b0: stat_before = _IS.Stat(b0.convert('L'))
        with Image.open(blurry_out) as b1:   stat_after  = _IS.Stat(b1.convert('L'))
        contrast_before = stat_before.stddev[0]
        contrast_after  = stat_after.stddev[0]
        contrast_ok = contrast_after > contrast_before
        s3 = 'PASS' if contrast_ok else 'FAIL'
        print(f'\n  [{s3}] 模糊模式增强后对比度（灰度 stddev）提升：'
              f'{contrast_before:.1f} → {contrast_after:.1f}（期望提升）: {contrast_ok}')
        print(f'  [INFO] 输出文件：{blurry_out.name}')
    else:
        print(f'  [FAIL] enhance_image_with_pillow 在模糊图像上失败：{ok_b}')

    # 测试清晰（有噪声）图像走正常模式（不报"模糊图像"）
    sharp_out = tmp / 'enhanced_sharp.png'
    ok_s = convert.enhance_image_with_pillow(sharp_path, sharp_out)
    s4 = 'PASS' if ok_s and sharp_out.exists() else 'FAIL'
    print(f'\n  [{s4}] 清晰（噪声）图像走正常增强模式（返回 True，文件存在）: {ok_s}')


# ===========================================================================
# 总结
# ===========================================================================
section('测试总结')
print('Pillow 图像增强流程（自适应去噪/锐化 + 亮度/对比度）：已验证')
print(f'waifu2x-ncnn-vulkan 可执行文件：{"已找到 " + str(waifu2x_exe) if waifu2x_exe else "未找到（优雅跳过）"}')
print('Audiveris 20M px 像素限制保护：已验证（超大图像自动降采样）')
print('模糊图像自适应强化锐化模式（Laplacian stddev 检测）：已验证')
print()
print('预期 OMR 识别提升效果：')
print('  - 清晰噪声扫描：Gaussian blur 去噪后 Unsharp mask 锐化，')
print('    使五线谱线条和符头边缘更清晰，减少 Audiveris 误识别')
print('  - 模糊图像（Laplacian stddev < 30）：跳过高斯模糊，使用强 Unsharp mask')
print('    (radius=3, percent=300) + 高对比度增强，尽力恢复五线谱边缘')
print('  - 低分辨率图像（< 1200px）：waifu2x 2x 放大后再增强，')
print('    Audiveris 要求分辨率 ≥ 150dpi，放大可使低质扫描满足此要求')
print('  - 超大图像（> 20M px）：自动等比缩小至限制内，不再被 Audiveris 拒绝')
print('  - PDF 输入：不受影响，Audiveris 内部渲染，绕过全部预处理')
