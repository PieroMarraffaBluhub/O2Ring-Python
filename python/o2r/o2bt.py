import struct
import queue
import warnings
import json

from .defines import *

from bleak import BleakScanner, BleakClient
import asyncio
import functools
from .o2pkt import o2pkt

# Suppress Bleak deprecation warnings
warnings.filterwarnings("ignore", category=FutureWarning)

class O2BTDevice(BleakClient):
  def busy(self):
    return self.pkt is not None

  def send_packet(self, pkt):
    self.pkt_queue.put(pkt)
    self._start_packet()

  def _start_packet(self):
    if self.pkt is None:
      try:
        self.pkt = self.pkt_queue.get(False)
      except:
        return

      pstr = self.pkt.packetify()

      asyncio.ensure_future(self._go_send(pstr))

  async def _go_send(self, buf):
    if self.disconnect_pending or not self.is_connected:
      return

    await self.write_gatt_char(self.write, buf[:20])

    if len(buf) > 20:
      asyncio.ensure_future(self._go_send(buf[20:]))

  async def _go_get_services(self):
    if self.disconnect_pending or not self.is_connected:
      return

    services = await self.get_services()

    if self.manager.verbose > 1:
      for service in services:
        for characteristic in service.characteristics:
          for descriptor in characteristic.descriptors:
            value = await self.read_gatt_descriptor(descriptor.handle)

    for s in self.services:
      if s.uuid == BLE_SERVICE_UUID:
        for c in s.characteristics:
          if c.uuid == BLE_READ_UUID:
            asyncio.ensure_future(self._go_enable_notifications(c))
          elif c.uuid == BLE_WRITE_UUID:
            self.write = c

  async def _go_enable_notifications(self, characteristic):
    async def on_characteristic_value_updated(sender, value):
      if self.pkt is None:
        return

      res = self.pkt.recv( value )
      if res is False: # waiting for mo
        return

      self.manager.queue.put_nowait((self.mac_address, "BTDATA", self.pkt))
      self.pkt = None
      self._start_packet()

    if self.disconnect_pending or not self.is_connected:
      return

    await self.start_notify(characteristic, on_characteristic_value_updated)

    self.manager.queue.put_nowait((self.mac_address, "READY",
      {"name": self.name, "mac": self.address, "self": self, "verbose": self.manager.verbose,
      "send": self.send_packet, "busy": self.busy, "disconnect": self.disconnect }))

  async def _go_connect(self):
    if self.is_connected:
      return

    if await super().connect():
      asyncio.ensure_future(self._go_get_services())

  def connect(self):
    asyncio.ensure_future(self._go_connect())

  async def disconnect_async(self):
    if not self.is_connected:
      return

    await super().disconnect()

  def disconnect(self):
    self.disconnect_pending = True
    asyncio.ensure_future(self.disconnect_async())

  def on_disconnect(self):
    self.manager.queue.put_nowait((self.mac_address, "DISCONNECT", self))

class O2DeviceManager:
  def __init__(self):
    self.pipe_down = []
    self.devices = {}
    self.scanner = BleakScanner(detection_callback=self.on_detection)
    self.verbose = 1  # Add verbose flag initialization

  async def start_discovery(self):
    await self.scanner.start()

  async def stop_discovery(self):
    await self.scanner.stop()

  def on_detection(self, device, advertisement_data):
    name = device.name or device.address
    uuids = advertisement_data.service_uuids if hasattr(advertisement_data, 'service_uuids') else None
    
    # Check if device is already known
    if device.address in self.devices:
      dev = self.devices[device.address]
      # Update name if available
      if device.name is not None and dev.name == device.address:
        dev.name = device.name
      # Update RSSI if available
      if hasattr(advertisement_data, 'rssi'):
        dev.rssi = advertisement_data.rssi
        dev.notified = True
      return

    # Check if device is a valid O2Ring
    valid = False
    if uuids is not None and BLE_MATCH_UUID in uuids and BLE_SERVICE_UUID in uuids:
      valid = True
    else:
      # We might not have the list of UUIDs yet, so also check by name
      names = ("Checkme_O2", "CheckO2", "SleepU", "SleepO2", "O2Ring", "WearO2", "KidsO2", "BabyO2", "Oxylink", 
              "O2", "O2RING", "O2 RING", "O2-RING", "O2_RING", "O2R", "O2-R", "O2_R")
      for n in names:
        if n.lower() in name.lower():
          valid = True
          break

    if valid:
      # Only print data for valid O2Ring devices
      device_data = {
        "type": "device",
        "name": name,
        "address": device.address,
        "rssi": advertisement_data.rssi if hasattr(advertisement_data, 'rssi') else None,
        "uuids": uuids if uuids else []
      }
      # Ensure we only print valid JSON and no empty lines
      try:
        json_str = json.dumps(device_data)
        if json_str.strip():  # Only print if not empty
          print(json_str, flush=True)  # Add flush to ensure immediate output
      except Exception as e:
        print(json.dumps({"type": "error", "message": f"Failed to serialize device data: {str(e)}"}), flush=True)

      dev = O2BTDevice(address_or_ble_device=device, timeout=10.0, disconnected_callback=O2BTDevice.on_disconnect)
      dev.mac_address = device.address
      dev.manager = self
      dev.name = name
      dev.notified = False
      dev.rssi = advertisement_data.rssi if hasattr(advertisement_data, 'rssi') else -999
      dev.write = None
      dev.disconnect_pending = False
      dev.pkt = None
      dev.pkt_queue = queue.Queue()
      self.devices[device.address] = dev

  async def connect_to_device(self, address):
    """Connect to a specific device by address"""
    if address in self.devices:
      dev = self.devices[address]
      dev.connect()
      return True
    return False
