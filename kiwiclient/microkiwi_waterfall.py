#!/usr/bin/python
# -*- coding: utf-8 -*-
""" Here is the IS0KYB microkiwi_waterfall script, modified to use python instead of jupyther notebook """

# python 2/3 compatibility
from __future__ import print_function
from __future__ import division

from optparse import OptionParser

import io
import socket
import struct
import time
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

from kiwi import wsclient

import mod_pywebsocket.common
from mod_pywebsocket.stream import Stream
from mod_pywebsocket.stream import StreamOptions


processfailed = False

cdict1 = {
    'red': ((0.0, 0.0, 0.0),
            (0.2, 0.0, 0.0),
            (0.4, 1.0, 1.0),
            (0.6, 1.0, 1.0),
            (0.8, 1.0, 1.0),
            (1.0, 1.0, 1.0)),

    'green': ((0.0, 0.0, 0.0),
              (0.2, 0.0, 0.0),
              (0.4, 1.0, 1.0),
              (0.6, 0.0, 0.0),
              (0.8, 0.0, 0.0),
              (1.0, 0.764, 0.764)),

    'blue': ((0.0, 0.0, 0.0),
             (0.2, 1.0, 1.0),
             (0.4, 0.0, 0.0),
             (0.6, 0.0, 0.0),
             (0.8, 1.0, 1.0),
             (1.0, 1.0, 1.0)),
}

cmap = LinearSegmentedColormap('SAColorMap', cdict1, 1024)

parser = OptionParser()
parser.add_option("-s", "--server", type=str,
                  help="server name", dest="server", default='192.168.1.82')
parser.add_option("-p", "--port", type=int,
                  help="port number", dest="port", default=8073)
parser.add_option("-l", "--length", type=int,
                  help="how many samples to draw from the server", dest="length", default=200)
parser.add_option("-z", "--zoom", type=int,
                  help="zoom factor", dest="zoom", default=0)
parser.add_option("-o", "--offset", type=int,
                  help="start frequency in kHz", dest="start", default=0)
parser.add_option("-v", "--verbose", type=int,
                  help="whether to print progress and debug info", dest="verbosity", default=0)
                  
options = vars(parser.parse_args()[0])
host = options['server']
port = options['port']

# the default number of bins is 1024
bins = 1024
zoom = options['zoom']
offset_khz = options['start']  # this is offset in kHz
full_span = 30000  # for a 30MHz kiwiSDR
if zoom > 0:
    span = full_span // 2.**zoom
else:
    span = full_span
rbw = span//bins
if offset_khz > 0:
    offset = (offset_khz+100)//(full_span//bins)*2**4*1000.
    offset = max(0, offset)
else:
    offset = 0
center_freq = span//2+offset_khz
now = b'(datetime.now()'
header = [center_freq, span, now]
header_bin = struct.pack("II26s", *header)

try:
    mysocket = socket.socket()
    mysocket.connect((host, port))
except:
    print("Failed to connect")
    exit()

uri = '/%d/%s' % (int(time.time()), 'W/F')
handshake = wsclient.ClientHandshakeProcessor(mysocket, host, port)
handshake.handshake(uri)
request = wsclient.ClientRequest(mysocket)
request.ws_version = mod_pywebsocket.common.VERSION_HYBI13
stream_option = StreamOptions()
stream_option.mask_send = True
stream_option.unmask_receive = False
mystream = Stream(request, stream_option)

# send a sequence of messages to the server, hardcoded for now
# max wf speed, no compression
msg_list = ['SET auth t=kiwi p=', 'SET zoom=%d start=%d' % (zoom, offset),
            'SET maxdb=0 mindb=-100', 'SET wf_speed=4', 'SET wf_comp=0']
for msg in msg_list:
    mystream.send_message(msg)
# number of samples to draw from server
length = options['length']
# create a numpy array to contain the waterfall data
wf_data = np.zeros((length, bins))
binary_wf_list = []
time = 0

while time < length:
    # receive one msg from server
    try:
        tmp = mystream.receive_message()
    except:
        processfailed = True
        break
    if b'W/F' in tmp:  # this is one waterfall line
        tmp = tmp[16:].replace(b"7", b"\xa0")  # remove some header from each msg & bug fix for blocked freq ranges
        print("received sample")
        if options['verbosity']:
            print(time),
        spectrum = np.array(struct.unpack('%dB' % len(tmp), tmp))  # convert from binary data to uint8
        # spectrum = np.ndarray(len(tmp), dtype='B', buffer=tmp)  # convert from binary data to uint8
        binary_wf_list.append(tmp)  # append binary data to be saved to file
        # wf_data[time, :] = spectrum-255 # mirror dBs
        wf_data[time, :] = spectrum
        wf_data[time, :] = -(255 - wf_data[time, :])  # dBm
        wf_data[time, :] = wf_data[time, :] - 13  # typical Kiwi wf cal
        time += 1
    else:  # this is chatter between client and server
        pass

try:
    mystream.close_connection(mod_pywebsocket.common.STATUS_GOING_AWAY)
    mysocket.close()
except Exception as e:
    print("exception: %s" % e)

avg_wf = np.mean(wf_data, axis=0)  # average over time
p95 = np.percentile(avg_wf, 95)
median = np.percentile(avg_wf, 50)
maxsig = np.max(wf_data)
minsig = np.min(wf_data)

# print "Waterfall with %d bins: median= %f dB, p95= %f dB - SNR= %f rbw= %f kHz" % (bins, median, p95,p95-median, rbw)
print("SNR: %i dB [median: %i dB, p95: %i dB, high: %i dBm, low: %i dBm]" % (p95 - median, median, p95, maxsig, minsig))

fd = io.BytesIO()  # no more file to write on disk
fd.write(header_bin)  # write the header info at the top
for line in binary_wf_list:
    fd.write(line)

fd.seek(0)
buff = fd.read()
header_len = 8 + 26  # 2 unsigned int for center_freq and span: 8 bytes PLUS 26 bytes for datetime
length = len(buff[header_len:])
n_t = length // bins
header = struct.unpack('2I26s', buff[:header_len])
data = struct.unpack('%dB' % length, buff[header_len:])
waterfall_array = np.reshape(np.array(data[:]), (n_t, bins))
waterfall_array -= 255
plt.figure(figsize=(14, 5))
plt.yticks(np.arange(0, 0, step=1))
plt.xlabel('MHz')
plt.xticks(np.linspace(0, bins, 31), np.linspace(0, 30, num=31, endpoint=True, dtype=int))
plt.pcolormesh(waterfall_array[:, 1:], cmap=cmap, vmin=minsig+30, vmax=maxsig+30)
if processfailed:
    plt.title("Sorry, measurement failed on this Kiwi, no slot available, try later")
else:
    plt.title("HF waterfall @ " + str(host) + " - [SNR: %i dB" % (p95 - median) + "]")
clb = plt.colorbar()
clb.ax.set_title('dBm')
plt.show()
