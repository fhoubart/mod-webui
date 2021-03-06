#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim: ai ts=4 sts=4 et sw=4 nu

import traceback

from shinken.log import logger

from .metamodule import MetaModule

class LogsMetaModule(MetaModule):

    _functions = ['get_ui_logs', 'get_ui_availability']
    _custom_log = "You should configure the module 'mongo-logs' in your broker to be able to display logs and availability."

    def __init__(self, modules, app):
        ''' Because it wouldn't make sense to use many submodules in this
            MetaModule, we only use the first one in the list of modules. 
            If there is no module in the list, we try to init a default module.
        '''
        self.app = app
        self.module = None
        if modules:
            if len(modules) > 1:
                logger.warning('[WebUI] Too much prefs modules declared (%s > 1). Using %s.' % (len(modules), modules[0]))
            self.module = modules[0]
        else:
            try:
                self.module = MongoDBLogs(app.modconf)
            except Exception as e:
                logger.warning('[WebUI] %s' % e)

    def is_available(self):
        return self.module is not None

    def get_ui_logs(self, name, logs_type=None, default=None, range_start=None, range_end=None):
        if self.is_available():
            return self.module.get_ui_logs(name, logs_type, range_start, range_end) or default
        return default

    def get_ui_availability(self, name, range_start=None, range_end=None, default=None):
        if self.is_available():
            return self.module.get_ui_availability(name, range_start, range_end) or default
        return default



import re

try:
    import pymongo
    from pymongo import MongoClient
except ImportError:
    logger.error('[WebUI-mongo-logs] Can not import pymongo and/or MongoClient'
                 'Your pymongo lib is too old. '
                 'Please install it with a 3.x+ version from '
                 'https://pypi.python.org/pypi/pymongo')
    raise

class MongoDBLogs():
    '''
    This module job is to get webui configuration data from a mongodb database:
    '''

    def __init__(self, mod_conf):
        self.uri = getattr(mod_conf, 'uri', 'mongodb://localhost')
        logger.info('[WebUI-mongo-logs] mongo uri: %s' % self.uri)
        
        self.replica_set = getattr(mod_conf, 'replica_set', None)
        if self.replica_set and int(pymongo.version[0]) < 3:
            logger.error('[WebUI-mongo-logs] Can not initialize module with '
                         'replica_set because your pymongo lib is too old. '
                         'Please install it with a 3.x+ version from '
                         'https://pypi.python.org/pypi/pymongo')
            return None
            
        self.database = getattr(mod_conf, 'database', 'shinken')
        self.username = getattr(mod_conf, 'username', None)
        self.password = getattr(mod_conf, 'password', None)
        logger.info('[WebUI-mongo-logs] database: %s' % self.database)

        self.logs_collection = getattr(mod_conf, 'logs_collection', 'logs')
        logger.info('[WebUI-mongo-logs] shinken logs collection: %s', self.logs_collection)
        
        self.hav_collection = getattr(mod_conf, 'hav_collection', 'availability')
        logger.info('[WebUI-mongo-logs] hosts availability collection: %s', self.hav_collection)
        
        #self.max_records = int(getattr(mod_conf, 'max_records', '200'))
        #logger.info('[WebUI-mongo-logs] max records: %s' % self.max_records)

        self.mongodb_fsync = getattr(mod_conf, 'mongodb_fsync', "True") == "True"
        
        self.is_connected = False
        self.con = None
        self.db = None

        logger.info("[WebUI-mongo-logs] Trying to open a Mongodb connection to %s, database: %s" % (self.uri, self.database))
        self.open()

    def open(self):
        try:
            if self.replica_set:
                self.con = MongoClient(self.uri, replicaSet=self.replica_set, fsync=self.mongodb_fsync)
            else:
                self.con = MongoClient(self.uri, fsync=self.mongodb_fsync)
            logger.info("[WebUI-mongo-logs] connected to mongodb: %s", self.uri)

            self.db = getattr(self.con, self.database)
            logger.info("[WebUI-mongo-logs] connected to the database: %s", self.database)
            
            if self.username and self.password:
                self.db.authenticate(self.username, self.password)
                logger.info("[WebUI-mongo-logs] user authenticated: %s", self.username)
                
            self.is_connected = True
            logger.info('[WebUI-mongo-logs] database connection established')
        except Exception, e:
            logger.warning("[WebUI-mongo-logs] Exception type: %s", type(e))
            logger.warning("[WebUI-mongo-logs] Back trace of this kill: %s", traceback.format_exc())
            # Depending on exception type, should raise ...
            self.is_connected = False
            
        return self.is_connected

    def close(self):
        self.is_connected = False
        self.conn.disconnect()

    # We will get in the mongodb database the logs
    def get_ui_logs(self, name, logs_type=None, range_start=None, range_end=None):
        if not self.db:
            logger.error("[mongo-logs] error Problem during init phase, no database connection")
            return None

        logger.info("[mongo-logs] get_ui_logs, name: %s", name)
        hostname = None
        service = None
        if name is not None:
            hostname = name
            if '/' in name:
                service = name.split('/')[1]
                hostname = name.split('/')[0]
        logger.debug("[mongo-logs] get_ui_logs, host/service: %s/%s", hostname, service)

        records=[]
        try:
            logger.info("[mongo-logs] Fetching records from database for host/service: '%s/%s'", hostname, service)

            query = []
            if hostname is not None:
                query.append( { "host_name" : { "$in": [ hostname ] }} )
            if service is not None:
                query.append( { "service_description" : { "$in": [ service ] }} )
            if logs_type and len(logs_type) > 0 and logs_type[0] != '':
                query.append({ "type" : { "$in": logs_type }})
            if range_start:
                query.append( { 'time': { '$gte': range_start } } )
            if range_end:
                query.append( { 'time': { '$lte': range_end } } )

            if len(query) > 0:
                logger.debug("[mongo-logs] Fetching records from database with query: '%s'", query)

                for log in self.db[self.logs_collection].find({'$and': query}).sort([
                                    ("time",pymongo.DESCENDING)]):
                    message = log['message']
                    m = re.search(r"\[(\d+)\] (.*)", message)
                    if m and m.group(2):
                        message = m.group(2)
                        
                    records.append({
                        "timestamp":    int(log["time"]),
                        "host":         log['host_name'],
                        "service":      log['service_description'],
                        "message":      message
                    })

            else:
                for log in self.db[self.logs_collection].find().sort([
                                    ("day",pymongo.DESCENDING)]):
                    message = log['message']
                    m = re.search(r"\[(\d+)\] (.*)", message)
                    if m and m.group(2):
                        message = m.group(2)
                        
                    records.append({
                        "timestamp":    int(log["time"]),
                        "host":         log['host_name'],
                        "service":      log['service_description'],
                        "message":      message
                    })

            logger.info("[mongo-logs] %d records fetched from database.", len(records))
        except Exception, exp:
            logger.error("[mongo-logs] Exception when querying database: %s", str(exp))

        return records

    # We will get in the mongodb database the host availability
    def get_ui_availability(self, name, range_start=None, range_end=None):
        if not self.db:
            logger.error("[mongo-logs] error Problem during init phase, no database connection")
            return None

        logger.debug("[mongo-logs] get_ui_availability, name: %s", name)
        hostname = None
        service = None
        if name is not None:
            hostname = name
            if '/' in name:
                service = name.split('/')[1]
                hostname = name.split('/')[0]
        logger.debug("[mongo-logs] get_ui_availability, host/service: %s/%s", hostname, service)

        records=[]
        try:
            logger.debug("[mongo-logs] Fetching records from database for host/service: '%s/%s'", hostname, service)

            query = []
            if hostname is not None:
                query.append( { "hostname" : { "$in": [ hostname ] }} )
            if service is not None:
                query.append( { "service" : { "$in": [ service ] }} )
            if range_start:
                query.append( { 'day_ts': { '$gte': range_start } } )
            if range_end:
                query.append( { 'day_ts': { '$lte': range_end } } )

            if len(query) > 0:
                logger.info("[mongo-logs] Fetching records from database with query: '%s'", query)

                for log in self.db[self.hav_collection].find({'$and': query}).sort([
                                    ("day",pymongo.DESCENDING), 
                                    ("hostname",pymongo.ASCENDING), 
                                    ("service",pymongo.ASCENDING)]):
                    if '_id' in log:
                        del log['_id']
                    records.append(log)
            else:
                for log in self.db[self.hav_collection].find().sort([
                                    ("day",pymongo.DESCENDING), 
                                    ("hostname",pymongo.ASCENDING), 
                                    ("service",pymongo.ASCENDING)]):
                    if '_id' in log:
                        del log['_id']
                    records.append(log)

            logger.debug("[mongo-logs] %d records fetched from database.", len(records))
        except Exception, exp:
            logger.error("[mongo-logs] Exception when querying database: %s", str(exp))

        return records
