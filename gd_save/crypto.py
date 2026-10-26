"""GDCX 流加密：恐怖黎明存档文件使用的加密方式。

存档里除了两类字段之外，全部由一个状态机加密；状态机从文件最开头的 4 字节
种子生长出来：

* ``seed``  —— 裸 uint32，原样存放；
* ``state`` —— ``seed ^ 0x55555555``，再加上一张 256 项的置换表，由状态循环
  右移并乘以 39916801 生成；
* 解一个字节：与状态低 8 位异或，然后把**原始**字节混入状态；uint32 则整体
  异或后，把 4 个原始字节依次混入；
* “裸状态”会把状态本身写出来并重置状态，正因如此，一个没解析的块仍然可以被
  跳过而不破坏它后面的数据。

参考资料：gdcx 项目的 docs/format.md
(https://codeberg.org/alex-ilchukov/gdcx)。
"""
from __future__ import annotations

import struct

MASK = 0xFFFFFFFF
SEED_XOR = 0x55555555
MUL = 39916801
MAGIC = 0x58434447  # 小端字节序下的 "GDCX"


def rotr(value: int, shift: int = 1) -> int:
    """把 32 位整数循环右移。"""
    shift &= 31
    return ((value >> shift) | (value << (32 - shift))) & MASK


def translation_table(seed: int) -> list[int]:
    """生成那张 256 项的置换表，用来把原始字节混入状态。"""
    table = [0] * 256
    value = rotr(seed ^ SEED_XOR, 1)
    for index in range(256):
        table[index] = (value * MUL) & MASK
        value = rotr(table[index], 1)
    return table


def seed_state(seed: int) -> int:
    return (seed ^ SEED_XOR) & MASK


class Decoder:
    """从加密的存档文件里读出明文数值。"""

    def __init__(self, data: bytes, offset: int = 0):
        if len(data) < 8:
            raise ValueError("文件太小，不可能是存档文件")
        self.data = data
        self.seed = int.from_bytes(data[0:4], "little")
        self.state = seed_state(self.seed)
        self.table = translation_table(self.seed)
        # 种子本身占用最前面的 4 字节
        self.pos = offset + 4

    # -- 基础操作 --------------------------------------------------------
    def _mix(self, raw: int) -> None:
        self.state ^= self.table[raw]

    def raw_byte(self) -> int:
        if self.pos >= len(self.data):
            raise EOFError("文件意外结束")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def byte(self) -> int:
        """解出一个加密字节。"""
        raw = self.raw_byte()
        plain = (self.state & 0xFF) ^ raw
        self._mix(raw)
        return plain

    def u32(self) -> int:
        """解出一个加密的 uint32。"""
        raw_bytes = self.bytes(4)
        plain = self.state ^ int.from_bytes(raw_bytes, "little")
        for raw in raw_bytes:
            self._mix(raw)
        return plain & MASK

    def i32(self) -> int:
        value = self.u32()
        return value - (1 << 32) if value & 0x80000000 else value

    def f32(self) -> float:
        return struct.unpack("<f", self.u32().to_bytes(4, "little"))[0]

    def size_u32(self) -> int:
        """块的 size 字段：只与状态异或，但**不**推进状态。"""
        raw_bytes = self.bytes(4)
        return (self.state ^ int.from_bytes(raw_bytes, "little")) & MASK

    def bare_state(self) -> int:
        """读出一个未加密的状态字，并直接采用它。"""
        self.state = int.from_bytes(self.bytes(4), "little")
        return self.state

    # -- 组合操作 --------------------------------------------------------
    def bytes(self, count: int) -> bytes:
        if count < 0:
            raise ValueError("长度为负数")
        if self.pos + count > len(self.data):
            raise EOFError(f"想读取 {count} 字节（位于 {self.pos}），但文件在 {len(self.data)} 处结束")
        chunk = self.data[self.pos:self.pos + count]
        self.pos += count
        return chunk

    def blob(self, count: int) -> bytes:
        """解出 ``count`` 个加密字节。"""
        return bytes(self.byte() for _ in range(count))

    def skip(self, count: int) -> None:
        """不解码直接向前跳（只有在遇到裸状态之前才成立）。"""
        self.bytes(count)

    def ascii_string(self) -> str:
        length = self.u32()
        return self.blob(length).decode("ascii", "replace")

    def utf16_string(self) -> str:
        length = self.u32()
        return self.blob(length * 2).decode("utf-16-le", "replace")

    def uuid(self) -> str:
        return self.blob(16).hex()


class Encoder:
    """:class:`Decoder` 的精确逆运算。"""

    def __init__(self, seed: int):
        self.seed = seed & MASK
        self.state = seed_state(self.seed)
        self.table = translation_table(self.seed)
        self.out = bytearray()

    @classmethod
    def for_archive(cls, seed: int) -> "Encoder":
        encoder = cls(seed)
        encoder.out += (seed & MASK).to_bytes(4, "little")
        return encoder

    # -- 基础操作 --------------------------------------------------------
    def _mix(self, raw: int) -> None:
        self.state ^= self.table[raw]

    def byte(self, value: int) -> None:
        raw = (self.state & 0xFF) ^ (value & 0xFF)
        self.out.append(raw)
        self._mix(raw)

    def u32(self, value: int) -> None:
        raw = (self.state ^ (value & MASK)) & MASK
        raw_bytes = raw.to_bytes(4, "little")
        self.out += raw_bytes
        for item in raw_bytes:
            self._mix(item)

    def i32(self, value: int) -> None:
        self.u32(value & MASK)

    def f32(self, value: float) -> None:
        self.u32(struct.unpack("<I", struct.pack("<f", value))[0])

    def bare_state(self, value: int) -> None:
        self.out += (value & MASK).to_bytes(4, "little")
        self.state = value & MASK

    def seal_state(self) -> None:
        """把当前状态当作裸状态写出去（自洽）。"""
        self.bare_state(self.state)

    def reserve(self, count: int) -> int:
        offset = len(self.out)
        self.out += b"\x00" * count
        return offset

    def patch_u32_at(self, offset: int, state_at_field: int, value: int) -> None:
        raw = (state_at_field ^ (value & MASK)) & MASK
        self.out[offset:offset + 4] = raw.to_bytes(4, "little")

    # -- 组合操作 --------------------------------------------------------
    def blob(self, data: bytes) -> None:
        for item in data:
            self.byte(item)

    def ascii_string(self, text: str) -> None:
        data = text.encode("ascii", "replace")
        self.u32(len(data))
        self.blob(data)

    def utf16_string(self, text: str) -> None:
        data = text.encode("utf-16-le")
        self.u32(len(data) // 2)
        self.blob(data)

    def uuid(self, hex_string: str) -> None:
        self.blob(bytes.fromhex(hex_string))

    @property
    def data(self) -> bytes:
        return bytes(self.out)
