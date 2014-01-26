# $Id$
# Copyright 2013 Matthew Wall

#==============================================================================
# Xively
#==============================================================================
# Upload data to Xively (aka COSM, aka Pachube)
# https://xively.com/
#
# Installation:
# 1) put this file in bin/user
# 2) add the following configuration stanza to weewx.conf
# 3) restart weewx
#
# [[Xively]]
#     token = TOKEN
#     feed = FEED_ID
#     station = station_name

import syslog
import time
import urllib
import urllib2

import weewx
import weewx.restx
from weeutil.weeutil import to_bool

try:
    import cjson as json
    # XXX: maintain compatibility w/ json module
    setattr(json, 'dumps', json.encode)
    setattr(json, 'loads', json.decode)
except Exception, e:
    try:
        import simplejson as json
    except Exception, e:
        import json

def logmsg(level, msg):
    syslog.syslog(level, 'xively: %s' % msg)

def logdbg(msg):
    logmsg(syslog.LOG_DEBUG, msg)

def loginf(msg):
    logmsg(syslog.LOG_INFO, msg)

def logerr(msg):
    logmsg(syslog.LOG_ERR, msg)

class Xively(weewx.restx.StdRESTbase):
    """Upload to a xively server."""

    _VERSION = 0.3
    _SERVER_URL = 'http://api.cosm.com/v2/feeds'
    _FORMATS = {'barometer'   : 'barometer %.3f',        # inHg
                'outTemp'     : 'temperature_out %.1f',  # F
                'outHumidity' : 'humidity_out %03.0f',   # %
#                'inTemp'      : 'temperature_in %.1f',  # F
#                'inHumidity'  : 'humidity_in %03.0f',   # %
                'windSpeed'   : 'windSpeed %.2f',        # mph
                'windDir'     : 'windDir %03.0f',        # compass degree
                'windGust'    : 'windGust %.2f',         # mph
                'dewpoint'    : 'dewpoint %.1f',         # F
                'rain24'      : 'rain24 %.2f',           # in
                'hourRain'    : 'hourRain %.2f',         # in
                'dayRain'     : 'dayRain %.2f',          # in
                'radiation'   : 'radiation %.2f',        # W/m^2
                'UV'          : 'UV %.2f'}               # number

    def __init__(self, engine, config_dict):
        """Initialize for uploading to Xively.

        token: unique token

        feed: the feed name

        Optional parameters:

        station: station identifier - if specified it will prefix data names
        Default is None

        server_url: URL of the server
        Default is the Smart Energy Groups site
        
        log_success: If True, log a successful post in the system log.
        Default is True.

        log_failure: If True, log an unsuccessful post in the system log.
        Default is True.

        max_backlog: How many records are allowed to accumulate in the queue
        before the queue is trimmed.
        Default is sys.maxint (essentially, allow any number).

        max_tries: How many times to try the post before giving up.
        Default is 3

        stale: How old a record can be and still considered useful.
        Default is None (never becomes too old).

        post_interval: How long to wait between posts.
        Default is None (post every record).

        timeout: How long to wait for the server to respond before giving up.
        Default is 60 seconds

        skip_upload: debugging option to display data but do not upload
        Default is False
        """
        super(Xively, self).__init__(engine, config_dict)
        try:
            site_dict = dict(config_dict['StdRESTful']['Xively'])
            site_dict['feed']
            site_dict['token']
        except KeyError, e:
            logerr("Data will not be posted: Missing option %s" % e)
            return
        self.server_url = site_dict.get('url', self._SERVER_URL)
        self.station = site_dict.get('station', None)

        self.archive_queue = Queue.Queue()
        self.archive_thread = XivelyThread(self.archive_queue, **site_dict)
        self.archive_thread.start()
        self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)
        loginf("Data will be uploaded to Xively")

    def new_archive_record(self, event):
        self.archive_queue.put(event.record)

class XivelyThread(weewx.restx.RESTThread):
    def __init__(self, queue, feed, token,
                 server_url=Xively._SERVER_URL, station=None, skip_upload=True,
                 log_success=True, log_failure=True, max_backlog=0,
                 stale=None, max_tries=3, post_interval=None, timeout=60):
        super(XivelyThread, self).__init__(queue,
                                           protocol_name='Xively',
                                           log_success=log_success,
                                           log_failure=log_failure,
                                           max_backlog=max_backlog,
                                           stale=stale,
                                           max_tries=max_tries,
                                           post_interval=post_interval,
                                           timeout=timeout)
        self.feed = feed
        self.token = token
        self.station = station
        self.server_url = server_url
        self.skip_upload = skip_upload

    def process_record(self, record, archive):
        r = self.augment_record(record, archive)
        url = self.get_url()
        data = self.get_data(r)
        if self.skip_upload:
            logdbg("skipping upload")
            return
        req = urllib2.Request(url, data)
        req.add_header("User-Agent", "weewx/%s" % weewx.__version__)
        req.add_header("X-PachubeApiKey", self.token)
        req.get_method = lambda: 'PUT'
        self.post_with_retries(req)

    def check_response(self, response):
        txt = response.read()
        if txt != '':
            raise weewx.restx.FailedPost(txt)

    def get_url(self):
        url = '%s/%s' % (self.server_url, self.feed)
        logdbg('url: %s' % url)
        return url
        
    def get_data(self, record):
        station = urllib.quote_plus(self.station) \
            if self.station is not None else None
        tstr = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time_ts))
        streams = {}
        for k in Xively._FORMATS:
            v = record[k]
            if v is not None:
                dskey = '%s_%s' % (station, k) if station is not None else k
                if not dskey in streams:
                    streams[dskey] = {'id':dskey, 'datapoints':[]}
                dp = {'at':tstr, 'value':v}
                streams[dskey]['datapoints'].append(dp)
        if len(streams.keys()) == 0:
            return None
        data = {
            'version':'1.0.0',
            'datastreams':[]
            }
        for k in streams.keys():
            data['datastreams'].append(streams[k])
        data = json.dumps(data)
        logdbg('data: %s' % data)
        return data