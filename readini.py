# -*- coding: utf-8 -*-
"""
 * Create by: yufei
 * Date: 2020/8/18
 * Time: 18:04
 * Name: 
 * Porpuse: 
 * Copyright © 2020年 Fei. All rights reserved.
"""
import configparser
import os

class ReadConfig:
    def __init__(self, config_path=None):
        if config_path is None:
            curpath = os.getcwd()
            config_path = os.path.join(curpath, 'config.ini')
        self.cf = configparser.ConfigParser()
        self.cf.read(config_path, encoding='utf-8')

    def get_markconfig(self, param):
        value = self.cf.get("javconfig", param)
        return value
