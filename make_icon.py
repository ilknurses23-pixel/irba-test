"""irba test simgesini (assets/irba.ico) ek paket olmadan üretir."""
import os
import struct


def rr_cov(px, py, x0, y0, x1, y1, r):
    """(px,py) noktasının yuvarlatılmış dikdörtgen içinde olup olmadığı (0/1)."""
    if px < x0 or px > x1 or py < y0 or py > y1:
        return 0.0
    cx = min(max(px, x0 + r), x1 - r)
    cy = min(max(py, y0 + r), y1 - r)
    return 1.0 if (px - cx) ** 2 + (py - cy) ** 2 <= r * r else 0.0


def render(n):
    ss = 4
    pix = []
    for y in range(n):
        row = []
        for x in range(n):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(ss):
                for sx in range(ss):
                    u = (x + (sx + 0.5) / ss) / n
                    v = (y + (sy + 0.5) / ss) / n
                    a = rr_cov(u, v, 0.04, 0.04, 0.96, 0.96, 0.22)
                    if not a:
                        continue
                    t = (u + v) / 2   # mavi → mor degrade
                    col = (int(79 + (124 - 79) * t), int(140 + (92 - 140) * t), int(255 + (230 - 255) * t))
                    # telefon gövdesi (beyaz)
                    if rr_cov(u, v, 0.31, 0.17, 0.69, 0.83, 0.07):
                        col = (255, 255, 255)
                        # ekran (koyu)
                        if rr_cov(u, v, 0.355, 0.24, 0.645, 0.70, 0.02):
                            col = (30, 41, 80)
                            # ekranda iki küçük "telefon" çizgisi (çoklu cihaz)
                            if 0.40 <= u <= 0.60 and (0.34 <= v <= 0.38 or 0.46 <= v <= 0.50):
                                col = (120, 170, 255)
                            if 0.40 <= u <= 0.52 and 0.58 <= v <= 0.62:
                                col = (62, 207, 142)
                        # ana tuş
                        if (u - 0.5) ** 2 + (v - 0.765) ** 2 <= 0.028 ** 2:
                            col = (30, 41, 80)
                    acc[0] += col[0]
                    acc[1] += col[1]
                    acc[2] += col[2]
                    acc[3] += 1
            k = ss * ss
            if acc[3]:
                row.append((int(acc[2] / acc[3]), int(acc[1] / acc[3]), int(acc[0] / acc[3]),
                            int(255 * acc[3] / k)))
            else:
                row.append((0, 0, 0, 0))
        pix.append(row)
    return pix


def bmp_entry(n):
    pix = render(n)
    hdr = struct.pack("<IiiHHIIiiII", 40, n, n * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    xor = b"".join(bytes(p) for row in reversed(pix) for p in row)
    mask_row = ((n + 31) // 32) * 4
    andm = b"\x00" * (mask_row * n)
    return hdr + xor + andm


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    blobs = [bmp_entry(n) for n in sizes]
    out = struct.pack("<HHH", 0, 1, len(sizes))
    off = 6 + 16 * len(sizes)
    for n, b in zip(sizes, blobs):
        out += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(b), off)
        off += len(b)
    out += b"".join(blobs)
    here = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.join(here, "assets"), exist_ok=True)
    p = os.path.join(here, "assets", "irba.ico")
    with open(p, "wb") as f:
        f.write(out)
    print("yazildi:", p, len(out), "bayt")
    for n in (40, 64):
        write_png(os.path.join(here, "assets", f"irba_{n}.png"), n)


def write_png(path, n):
    import zlib
    pix = render(n)
    raw = b"".join(b"\x00" + b"".join(bytes((p[2], p[1], p[0], p[3])) for p in row) for row in pix)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    print("yazildi:", path)


if __name__ == "__main__":
    main()
