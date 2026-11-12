# -*- coding: utf-8 -*-
"""
홈 화면 아이콘 만들기 (파이썬 기본 기능만 사용)

  python tools/make_icons.py

assets/ 폴더에 PNG 아이콘을 만듭니다. 그림을 바꾸고 싶으면 아래 색·모양 값을 고친 뒤 다시 실행.
모양: 남색 바탕 + 흰색 꺾은선(지표 추이) + 초록·주황·빨강 구간 막대 + 현재 위치 ●
"""
import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "assets"
BG = (31, 58, 95)        # 남색 바탕
LINE = (255, 255, 255)   # 꺾은선
SEGS = [(46, 160, 95), (232, 163, 23), (220, 75, 62)]  # 초록·주황·빨강
SS = 3                   # 가장자리 부드럽게 (3x3 샘플)

# 좌표는 0~1 비율 (크기와 상관없이 같은 그림)
CHART = [(0.18, 0.58), (0.34, 0.44), (0.47, 0.52), (0.63, 0.31), (0.82, 0.24)]
LINE_W = 0.045
BAR_Y, BAR_H, BAR_X0, BAR_X1 = 0.74, 0.075, 0.16, 0.84
DOT = (0.36, BAR_Y + BAR_H / 2, 0.07)  # 현재 위치 (x, y, 반지름)


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def color_at(x, y):
    # 현재 위치 점 (흰 테두리 + 남색 속)
    d = math.hypot(x - DOT[0], y - DOT[1])
    if d <= DOT[2] * 0.55:
        return BG
    if d <= DOT[2]:
        return LINE
    # 구간 막대 (양 끝 둥글게)
    r = BAR_H / 2
    cy = BAR_Y + r
    cx = min(max(x, BAR_X0 + r), BAR_X1 - r)
    if math.hypot(x - cx, y - cy) <= r:
        k = min(2, int((x - BAR_X0) / (BAR_X1 - BAR_X0) * 3))
        return SEGS[max(0, k)]
    # 꺾은선
    for (ax, ay), (bx, by) in zip(CHART, CHART[1:]):
        if seg_dist(x, y, ax, ay, bx, by) <= LINE_W / 2:
            return LINE
    return BG


def render(size):
    rows = []
    n = SS * SS
    for j in range(size):
        row = bytearray([0])  # PNG 줄 시작 표시
        for i in range(size):
            acc = [0, 0, 0]
            for sj in range(SS):
                for si in range(SS):
                    c = color_at((i + (si + 0.5) / SS) / size, (j + (sj + 0.5) / SS) / size)
                    acc[0] += c[0]; acc[1] += c[1]; acc[2] += c[2]
            row += bytes(round(a / n) for a in acc)
        rows.append(bytes(row))
    return b"".join(rows)


def write_png(path, size):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = render(size)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    path.write_bytes(png)
    print(f"만듦: {path.name} ({size}x{size})")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, size in [("apple-touch-icon.png", 180), ("icon-192.png", 192), ("icon-512.png", 512), ("favicon.png", 32)]:
        write_png(OUT / name, size)
