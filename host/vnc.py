#!/usr/bin/env python3
"""Minimal RFB (VNC) client: grab the screen, click, type. For driving the QEMU guest headlessly.

  vnc.py shot OUT.png [--port 5907]
  vnc.py click X Y [--right] [--double]
  vnc.py move X Y
  vnc.py type "text"
  vnc.py key NAME            (Return, Escape, Tab, Super, ...)
Coordinates are framebuffer pixels.
"""
import socket, struct, sys, time

PORT = 5907
KEYS = {'Return': 0xff0d, 'Escape': 0xff1b, 'Tab': 0xff09, 'BackSpace': 0xff08, 'Super': 0xffeb,
        'Left': 0xff51, 'Up': 0xff52, 'Right': 0xff53, 'Down': 0xff54, 'Shift': 0xffe1, 'Control': 0xffe3,
        'Alt': 0xffe9, 'space': 0x20, 'Home': 0xff50, 'End': 0xff57}


class VNC:
    def __init__(self, port=PORT, host='127.0.0.1'):
        self.s = socket.create_connection((host, port), timeout=30)
        v = self._rd(12); self.s.sendall(b'RFB 003.008\n')
        n = self._rd(1)[0]; types = self._rd(n); assert 1 in types, types
        self.s.sendall(b'\x01'); assert struct.unpack('>I', self._rd(4))[0] == 0
        self.s.sendall(b'\x01')                                      # shared
        self.w, self.h = struct.unpack('>HH', self._rd(4)); self._rd(16)
        self._rd(struct.unpack('>I', self._rd(4))[0])
        # 32 bpp true colour, little endian, R<<16 G<<8 B
        self.s.sendall(struct.pack('>BxxxBBBBHHHBBBxxx', 0, 32, 24, 0, 1, 255, 255, 255, 16, 8, 0))
        self.s.sendall(struct.pack('>BxHi', 2, 1, 0))                # raw only

    def _rd(self, n):
        b = b''
        while len(b) < n:
            c = self.s.recv(n - len(b))
            if not c: raise EOFError
            b += c
        return b

    def shot(self, out):
        from PIL import Image
        self.s.sendall(struct.pack('>BBHHHH', 3, 0, 0, 0, self.w, self.h))
        buf = bytearray(self.w * self.h * 4)
        while True:
            t = self._rd(1)[0]
            if t == 0:
                self._rd(1); n = struct.unpack('>H', self._rd(2))[0]
                for _ in range(n):
                    x, y, w, h, enc = struct.unpack('>HHHHi', self._rd(12))
                    if enc == 0:
                        d = self._rd(w * h * 4)
                        for r in range(h):
                            o = ((y + r) * self.w + x) * 4; buf[o:o + w * 4] = d[r * w * 4:(r + 1) * w * 4]
                    elif enc == -223:
                        self.w, self.h = w, h; buf = bytearray(w * h * 4)
                    else:
                        raise RuntimeError(f'unsupported encoding {enc}')
                break
            elif t == 2: pass
            elif t == 3: self._rd(1); self._rd(struct.unpack('>I', self._rd(4))[0])
            else: raise RuntimeError(f'message {t}')
        im = Image.frombuffer('RGBX', (self.w, self.h), bytes(buf), 'raw', 'BGRX', 0, 1).convert('RGB')
        im.save(out); return im.size

    def pointer(self, x, y, mask=0):
        self.s.sendall(struct.pack('>BBHH', 5, mask, x, y))

    def click(self, x, y, button=1, double=False):
        self.pointer(x, y, 0); time.sleep(0.3)
        for _ in range(2 if double else 1):
            self.pointer(x, y, button); time.sleep(0.12); self.pointer(x, y, 0); time.sleep(0.12)

    def wheel(self, x, y, down=True, n=3):
        self.pointer(x, y, 0); time.sleep(0.2)
        for _ in range(n):
            self.pointer(x, y, 16 if down else 8); time.sleep(0.05); self.pointer(x, y, 0); time.sleep(0.25)

    def key(self, sym, down=True):
        self.s.sendall(struct.pack('>BBxxI', 4, 1 if down else 0, sym))

    def press(self, sym):
        self.key(sym, True); time.sleep(0.05); self.key(sym, False); time.sleep(0.05)

    def type(self, text):
        for ch in text: self.press(KEYS.get(ch, ord(ch)))


if __name__ == '__main__':
    a = sys.argv[1:]
    if '--port' in a:
        i = a.index('--port'); PORT = int(a[i + 1]); del a[i:i + 2]
    c = VNC(PORT)
    if a[0] == 'shot': print(c.shot(a[1]))
    elif a[0] == 'click': c.click(int(a[1]), int(a[2]), 3 if '--right' in a else 1, '--double' in a)
    elif a[0] == 'wheel': c.wheel(int(a[1]), int(a[2]), a[3] != 'up', int(a[4]) if len(a) > 4 else 3)
    elif a[0] == 'move': c.pointer(int(a[1]), int(a[2]))
    elif a[0] == 'type': c.type(a[1])
    elif a[0] == 'key': c.press(KEYS.get(a[1], ord(a[1][0])))
    time.sleep(0.3)
