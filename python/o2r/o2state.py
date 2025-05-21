import time, json, pprint, struct, os.path, datetime

from .defines import *
from .o2pkt import o2pkt
from .o2cmd import o2cmd
from .o2file import *
import boto3
import json
import os

class o2state:
    def __init__( self, name, data, args ):
        self.name = name
        #self.dev = data['self']
        self.verbose = data['verbose']
        self.send_func = data['send']
        self.busy_func = data['busy']
        self.disconnect_func = data['disconnect']
        self.args = args
        self.next_read = 0
        self.need_cfg = False
        self.sent_cfg = False
        self.quiet_cfg = False
        self.want_files = []
        self.read_file_out = None
        self.read_file_in = None
        self.read_block = None
        self.read_fp = None
        self.read_size = 0
        self.read_want = 0
        self.no_finger_count = 0
        self.disconnect_at = 1

        self.send_func( o2pkt(CMD_INFO) )
        self.req_time_str = time.strftime( TIME_FORMAT )
        #self.send_func( o2pkt(CMD_PING) )
        #self.send_func( o2pkt(CMD_READ_SENSORS) )

    def recv( self, pkt ):
        if( self.verbose > 2 ):
            pass

        if( pkt.recv_cmd != 0 ):
            pass

        if( pkt.cmd == CMD_READ_SENSORS ):
            self.next_read = time.time() + 2.0

            # ffffff00000000600005000000
            # ffffff00000000620100000000
            # 624200000000006100920a0100
            # oohh..........bbccmmaaff..

            #(o2, hr, u1, u2) = struct.unpack( '<BBBB', pkt.recv_data )

            o2 = int(pkt.recv_data[0])
            hr = int(pkt.recv_data[1])
            # [2]
            # [3]
            # [4]
            # [5]
            # [6]
            batt = int(pkt.recv_data[7])
            charging = int( pkt.recv_data[8] )
            motion = int(pkt.recv_data[9])
            hr_strength = int(pkt.recv_data[10]) # heart rate signal strength
            finger_present = bool( pkt.recv_data[11] )
            # [12]

            #batts = str(batt) + '%' + ('++' if charging else '')

            if( charging > 0 ):
                if( charging == 1 ):
                    batts = str(batt) + '%++'
                elif( charging == 2 ):
                    batts = 'CHGD'
                else:
                    batts = ('% 3d' % batt) + '%-' + str(charging)

            else:
                batts = ('% 3d' % batt) + '%'

            data = {"type": "data","name": self.name, "o2": o2, "hr": hr, "hr strength": hr_strength, "motion": motion, "battery": batts}
            print(json.dumps(data))

            if( (o2 > 100) or (o2 < 10) or (not finger_present) ):
                self.no_finger_count += 1
                if( (self.no_finger_count > self.disconnect_at) and (not self.args.keep_going) ):
                    self.disconnect_func()
                else:
                    self.quiet_cfg = True
                    self.send_func( o2pkt(CMD_INFO) )
                    self.req_time_str = time.strftime( TIME_FORMAT )
            else:
                self.no_finger_count = 0
                self.disconnect_at = 11
                
        elif( pkt.cmd == CMD_INFO ):
            self.current_cfg = json.loads( pkt.recv_data.decode('ascii').rstrip( ' \t\r\n\0' ) )

            if( not self.quiet_cfg ):
                pass

            o2_time = datetime.datetime(*time.strptime( self.current_cfg['CurTIME'], TIME_FORMAT )[:6])
            sent_time = datetime.datetime(*time.strptime( self.req_time_str, TIME_FORMAT )[:6])
            tdelta = (o2_time - sent_time).total_seconds()

            if( abs(tdelta) > 1.0 ):
                pass

            self.need_cfg = False
            self.check_settings( )

            self.add_files( self.current_cfg['FileList'] )
            self.get_file()

            if( (self.next_read < 1) and (self.read_file_in is None) ):
                self.next_read = time.time() + 1.0

        elif( pkt.cmd == CMD_FILE_OPEN ):
            if( pkt.recv_cmd != 0 ):
                self.send_func( o2pkt(CMD_FILE_CLOSE) )
                self.next_read = time.time() + 2.0
                return

            self.read_block = 0
            self.next_read = 0
            self.read_want = self.read_size = struct.unpack( '<I', pkt.recv_data )[0]
            if( self.verbose > 0 ):
                ostr = '[%s] File %s Opened, Size %d ' % (self.name, self.read_file_in, self.read_size)
                pad = 99 - len(ostr)
                if( pad > 0 ):
                    ostr += ('-' * pad) + '|'
                self.read_percent = 1
            self.send_func( o2pkt(CMD_FILE_READ, block=self.read_block) )
            self.read_fp = None
        elif( pkt.cmd == CMD_FILE_READ ):
            self.read_want -= len( pkt.recv_data )
            self.read_block += 1
            cur_percent = round(((self.read_size - self.read_want) / self.read_size) * 100)
            if( (self.verbose > 0) and (cur_percent != self.read_percent) ):
                padc = ''
                if( cur_percent == 100 ):
                    pad = 99 - self.read_percent
                    if( pad > 0 ):
                        padc = '=' * pad
                    padc += '|'
                else:
                    pad = cur_percent - self.read_percent
                    padc = '=' * pad

            self.read_percent = cur_percent

            if( self.read_want > 0 ):
                self.next_read = 0
                self.send_func( o2pkt(CMD_FILE_READ, block=self.read_block) )
                if( self.verbose > 2 ):
                    pass
            else:
                self.send_func( o2pkt(CMD_FILE_CLOSE) )

                self.read_fp = None
                self.read_file_in = None
                self.read_file_out = None
                self.get_file()

            if( self.verbose > 3 ):
                pass
        elif( pkt.cmd == CMD_FILE_CLOSE ):
            if( self.read_file_in is None ):
                self.send_func( o2pkt(CMD_READ_SENSORS) )
        elif( pkt.cmd == CMD_CONFIG ):
            if( pkt.recv_cmd != 0 ):
                pass
        else:
            pass

    def check( self ):
        if self.busy_func():
            return

        if( self.next_read > 0 and time.time() >= self.next_read ):
            self.next_read = 0
            if( self.need_cfg ):
                self.send_func( o2pkt(CMD_INFO) )
                self.req_time_str = time.strftime( TIME_FORMAT )
            else:
                self.next_read = time.time() + 2.0
                self.send_func( o2pkt(CMD_READ_SENSORS) )

    def add_files( self, flist ):
        self.want_files.extend( [i for i in flist.split(',') if i] )

    def get_next_filename( self ):
        if( len( self.want_files ) > 0 ):
            return self.want_files.pop(0)

        return None

    def get_file( self ):
        if( self.read_file_in is not None ):
            return

        ext = self.args.ext if self.args.ext is not None else 'vld'
        prefix = self.args.prefix if self.args.prefix is not None else ('%s - ' % self.name)
        fname = self.get_next_filename()

        while( fname is not None ):
            ofname = prefix + fname + ('.' if len(ext) > 0 else '') + ext
            if( not os.path.exists(ofname) ):
                break

            fname = self.get_next_filename()

        if( fname is not None ):
            self.read_file_out = None
            self.read_file_in = fname
            self.read_percent = 0
            self.read_block = self.read_want = self.read_size = 0
            if( self.verbose > 0 ):
                ostr = '[%s] Requesting File %s, saving to "%s"' % (self.name, fname, ofname)
            fname += chr(0)
            self.send_func( o2pkt(CMD_FILE_OPEN, block=self.read_block, data=fname) )
        #else:
        #    self.send_func( o2pkt(CMD_READ_SENSORS) )

    def close( self ):
        self.next_read = 0
        self.want_files = [ ]
        self.read_want = 0
        #self.dev.disconnect()

    def check_settings( self ):
        if( self.sent_cfg ):
            return

        check = {}
        want_o2 = 2
        if( self.args.o2_alert is not None ):
            if( self.args.o2_alert < 1 ):
                check['OxiSwitch'] = 0
            elif( self.args.o2_alert <= 100 ):
                check['CurOxiThr'] = self.args.o2_alert
                check['OxiSwitch'] = 1

        if( self.args.hr_alert_high is not None ):
            if( self.args.hr_alert_high < 1 ):
                check['HRSwitch'] = 0
            elif( self.args.hr_alert_high <= 200 ):
                check['HRSwitch'] = 1
                check['HRHighThr'] = self.args.hr_alert_high

        if( self.args.hr_alert_low is not None ):
            if( self.args.hr_alert_low < 1 ):
                if 'HRSwitch' not in check:
                    check['HRSwitch'] = 0
            elif( self.args.hr_alert_low <= 200 ):
                check['HRSwitch'] = 1
                check['HRLowThr'] = self.args.hr_alert_low

        # FIXME flip low/high if reversed

        if( self.args.vibrate is not None ):
            check['CurMotor'] = self.args.vibrate

        if( self.args.screen is not None ):
            if( self.args.screen ):
                check['LightingMode'] = 2
            else:
                check['LightingMode'] = 0

        if( self.args.brightness is not None ):
            check['LightStr'] = self.args.brightness

        #if( self.args.pedtar is not None ):
        #    check['CurPedtar'] = self.args.pedtar

        if( 'HRLowThr' in check ):
            if( 'HRHighThr' in check ):
                if( check['HRLowThr'] > check['HRHighThr'] ):
                    i = check['HRHighThr']
                    check['HRHighThr'] = check['HRLowThr']
                    check['HRLowThr'] = i
            else:
                if( check['HRLowThr'] > int(self.current_cfg['HRHighThr']) ):
                    check['HRHighThr'] = check['HRLowThr']
                    #check['HRLowThr'] = int(self.current_cfg['HRHighThr'])
                    del check['HRLowThr']
        elif( 'HRHighThr' in check ):
            if( check['HRHighThr'] < int(self.current_cfg['HRLowThr']) ):
                check['HRLowThr'] = check['HRHighThr']
                #check['HRHighThr'] = int(self.current_cfg['HRLowThr'])
                del check['HRHighThr']

        update = {}

        for i in check:
            if( str(check[i]) != self.current_cfg[i] ):
                k = 'Set'
                if( i[:3] == 'Cur' ):
                    k += i[3:]
                else:
                    k += i

                update[k] = check[i]

        if( len(update) > 0 ):
            self.send_func( o2cmd.SetConfig(update) )
            self.need_cfg = True
            self.sent_cfg = True
            self.quiet_cfg = False


