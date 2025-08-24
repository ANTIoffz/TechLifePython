from __future__ import annotations

import struct
from functools import reduce
from typing import Optional, Union, Tuple

import paho.mqtt.client as mqtt
from pydantic.color import Color
from pydantic import ValidationError

ColorLike = Union[str, Tuple[int, int, int], Color]


class TechlifeLED:
    _FRAME = struct.Struct("<B6H3B")

    _CMD_SET_STATIC = 0x28
    _TAIL_SET_STATIC = 0x29
    _FLAG_SET_STATIC = 0x0F

    _CMD_ANIMATE = 0x66
    _SUBMODE_ANIMATE = 0x25
    _TAIL_ANIMATE = 0x99

    _CMD_POWER = 0xFA
    _SUB_ON = 0x23
    _SUB_OFF = 0x24
    _TAIL_POWER = 0xFB

    _CMD_UPDATE = 0xA9
    _SUB_UPDATE = 0xF0
    _TAIL_UPDATE = 0x9A

    def __init__(
        self,
        mqtt_server: str,
        mac_addr: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        client_id: Optional[str] = None,
        keepalive: int = 60,
        topic_prefix: str = "dev_sub_",
        qos: int = 0,
    ) -> None:
        self.server = mqtt_server
        self.device_mac = mac_addr
        self.topic = f"{topic_prefix}{self.device_mac}"
        self.qos = int(qos)

        self._on = False
        self._brightness = 1.0
        self._color = Color("#ffffff")
        self._animation_speed = 99

        self.client = mqtt.Client(client_id=client_id)
        if username:
            self.client.username_pw_set(username, password)
        self._keepalive = keepalive
        self._connected = False

        def _on_connect(_client, _userdata, _flags, rc):
            self._connected = (rc == 0)

        def _on_disconnect(_client, _userdata, rc):
            self._connected = False

        self.client.on_connect = _on_connect
        self.client.on_disconnect = _on_disconnect

    def connect(self) -> None:
        self.client.connect(self.server, keepalive=self._keepalive)
        self.client.loop_start()

    def close(self) -> None:
        try:
            self.client.loop_stop()
        finally:
            self.client.disconnect()

    def on(self) -> None:
        frame = self._FRAME.pack(
            self._CMD_POWER,
            self._SUB_ON, 0, 0, 0, 0, 0,
            0, 0, self._TAIL_POWER
        )
        self._send_with_checksum(frame)
        self._on = True

    def off(self) -> None:
        frame = self._FRAME.pack(
            self._CMD_POWER,
            self._SUB_OFF, 0, 0, 0, 0, 0,
            0, 0, self._TAIL_POWER
        )
        self._send_with_checksum(frame)
        self._on = False

    def is_on(self) -> bool:
        return self._on

    def set_color(self, color: ColorLike) -> None:
        try:
            self._color = color if isinstance(color, Color) else Color(color)
        except ValidationError as e:
            raise ValueError(f"Invalid color: {color}") from e
        self._apply_static()

    def set_brightness(self, value: int) -> None:
        value = max(0, min(100, int(value)))
        self._brightness = value / 100.0
        self._apply_static()

    def get_brightness(self) -> int:
        return int(round(self._brightness * 100))

    def set_animation_speed(self, speed: int) -> None:
        speed = max(1, min(100, int(speed)))
        self._animation_speed = speed

    def animate(self, enabled: bool) -> None:
        if enabled:
            device_speed = (100 - self._animation_speed) * 255 // 100
            frame = self._FRAME.pack(
                self._CMD_ANIMATE,
                self._SUBMODE_ANIMATE, device_speed, 0x64, 0, 0, 0,
                0, 0, self._TAIL_ANIMATE
            )
            self._send_with_checksum(frame)
        else:
            self._apply_static()

    def update(self) -> None:
        frame = self._FRAME.pack(
            self._CMD_UPDATE,
            self._SUB_UPDATE, 0, 0, 0, 0, 0,
            0, 0, self._TAIL_UPDATE
        )
        self._send_with_checksum(frame)
        self._on = True

    def _apply_static(self) -> None:
        r, g, b = self._color.as_rgb_tuple(alpha=False)

        def level(x: int) -> int:
            return int((x * 10000 * self._brightness) // 255)

        red, green, blue = level(r), level(g), level(b)
        brightness_0_100 = int(round(100 * self._brightness))

        frame = self._FRAME.pack(
            self._CMD_SET_STATIC,
            red, green, blue, 0, 0, brightness_0_100,
            self._FLAG_SET_STATIC, 0, self._TAIL_SET_STATIC
        )
        self._send_with_checksum(frame)

    @staticmethod
    def _insert_checksum(frame: bytes) -> bytearray:
        payload = bytearray(frame)
        checksum = reduce(lambda x, y: x ^ y, payload[1:14]) & 0xFF
        payload[14] = checksum
        return payload

    def _send_with_checksum(self, frame: bytes) -> None:
        payload = self._insert_checksum(frame)
        self.client.publish(self.topic, payload, qos=self.qos)
        