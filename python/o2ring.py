import o2r
import threading
import time
import queue
import traceback
import argparse
import json
import sys
import asyncio

# === Variabile globale per il loop principale ===
main_loop = None

# === Command queue per i comandi da stdin ===
command_queue = asyncio.Queue()

# === Funzione di lettura stdin in thread separato ===
def reader_thread():
    global main_loop
    while True:
        line = sys.stdin.readline()
        if not line:
            continue
        try:
            cmd = json.loads(line.strip())
            asyncio.run_coroutine_threadsafe(command_queue.put(cmd), main_loop)
        except Exception as e:
            print(json.dumps({"type": "error", "message": str(e)}), flush=True)

# === Helper parsing funzioni ===
def str2bool(v):
    if isinstance(v, bool):
        return v
    if v.lower() in ('yes', 'true', 't', 'y', '1', 'on'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0', 'off'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')

def str2bright(v):
    if v.lower() in ('l', '0'):
        return 0
    elif v.lower() in ('m', '1'):
        return 1
    elif v.lower() in ('h', '2'):
        return 2
    else:
        raise argparse.ArgumentTypeError('L/M/H or l/m/h or 0-2 expected.')

# === Funzione principale ===
async def main():
    arg_parser = argparse.ArgumentParser(description="O2Ring BLE Downloader")
    arg_parser.add_argument('--verbose', '-v', action='count', default=0)
    arg_parser.add_argument('--multi', '-m', action='store_true')
    arg_parser.add_argument('--keep-going', '-k', action='store_true')
    arg_parser.add_argument('--ext', '-e')
    arg_parser.add_argument('--prefix', '-p')
    arg_parser.add_argument('--scan', type=int)
    arg_parser.add_argument('--csv', action="store_true")
    arg_parser.add_argument('--connect', metavar='MAC_ADDRESS')
    arg_parser.add_argument('--o2-alert', type=int, metavar='[0-95]', choices=range(0,101))
    arg_parser.add_argument('--hr-alert-high', type=int, metavar='[0-200]', choices=range(0,201))
    arg_parser.add_argument('--hr-alert-low', type=int, metavar='[0-200]', choices=range(0,201))
    arg_parser.add_argument('--vibrate', type=int, metavar='[1-100]', choices=range(1,101))
    arg_parser.add_argument('--screen', type=str2bool, metavar='[bool]')
    arg_parser.add_argument('--brightness', type=str2bright, metavar='[L/M/H or 0-2]', choices=range(0,3))

    args = arg_parser.parse_args()

    manager = o2r.O2DeviceManager()
    manager.verbose = args.verbose
    manager.queue = asyncio.Queue()
    rings = {}
    scanning = False
    want_exit = False
    run = True
    stop_scanning_at = 0

    threading.Thread(target=reader_thread, daemon=True).start()

    if args.scan and args.scan > 0:
        stop_scanning_at = time.time() + args.scan

    if args.connect:
        try:
            await manager.connect_to_device(args.connect)
            print(json.dumps({"type": "connected", "address": args.connect}), flush=True)
        except Exception as e:
            print(json.dumps({"type": "error", "message": str(e)}), flush=True)
            return

    await manager.start_discovery()
    multi = args.multi

    while run:
        try:
            try:
                cmd_from_queue = command_queue.get_nowait()
                if cmd_from_queue.get('type') == 'connect' and cmd_from_queue.get('address'):
                    address = cmd_from_queue['address']
                    if address in manager.devices:
                        dev = manager.devices[address]
                        dev.connect()
                        print(json.dumps({
                            'type': 'status',
                            'message': f'Connecting to {dev.name}'
                        }), flush=True)
            except asyncio.QueueEmpty:
                pass

            cmd = await asyncio.wait_for(manager.queue.get(), timeout=1.0)

        except asyncio.TimeoutError:
            cmd = None
        except asyncio.CancelledError:
            run = False
            break
        except Exception as e:
            print(json.dumps({"type": "error", "message": str(e)}), flush=True)
            run = False
            break

        if cmd:
            (ident, command, data) = cmd

            if command == 'READY':
                if 'verbose' not in data:
                    data['verbose'] = args.verbose + 1
                if ident in rings:
                    rings[ident].close()
                rings[ident] = o2r.o2state(data['name'], data, args)
                if not multi and scanning:
                    await manager.stop_discovery()
                    scanning = False
            elif command == 'DISCONNECT':
                if ident in rings:
                    rings[ident].close()
                    del rings[ident]
                    if not scanning and len(rings) < 1:
                        want_exit = True
            elif command == 'BTDATA':
                if ident in rings:
                    rings[ident].recv(data)

        for r in list(rings):
            rings[r].check()

        if want_exit and len(rings) == 0:
            run = False

        if stop_scanning_at > 0 and time.time() >= stop_scanning_at:
            stop_scanning_at = 0
            if scanning:
                scanning = False
                await manager.stop_discovery()
            if len(rings) < 1:
                want_exit = True
                run = False

    if scanning:
        await manager.stop_discovery()

    to_disconnect = []
    for dev in manager.devices.values():
        if dev.is_connected:
            to_disconnect.append(dev.disconnect_async())

    await asyncio.gather(*to_disconnect)
    await asyncio.sleep(0.5)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main_loop = asyncio.get_event_loop()  # <<< Imposta il loop principale
    task = loop.create_task(main())
    try:
        loop.run_until_complete(task)
    except KeyboardInterrupt:
        task.cancel()
        loop.run_until_complete(task)
        task.exception()
