# -*- coding: utf_8 -*-
"""Module for iOS IPA Binary Analysis."""

import logging
import os
import platform
import stat
import subprocess

from django.conf import settings

from macholib.mach_o import (CPU_TYPE_NAMES, MH_CIGAM_64, MH_MAGIC_64,
                             get_cpu_subtype)
from macholib.MachO import MachO

from MobSF.utils import is_file_exists

from StaticAnalyzer.views.ios.otool_analysis import (
    get_otool_out,
    otool_analysis,
)
from StaticAnalyzer.views.ios.rules import (
    ipa_rules,
)
from StaticAnalyzer.views.rule_matchers import (
    binary_rule_matcher,
)
from StaticAnalyzer.tools.strings import strings_util

logger = logging.getLogger(__name__)


def detect_bin_type(libs):
    """Detect IPA binary type."""
    if any('libswiftCore.dylib' in itm for itm in libs):
        return 'Swift'
    else:
        return 'Objective C'


def class_dump(tools_dir, bin_path, app_dir, bin_type):
    """Running Classdumpz on binary."""
    try:
        classdump = b''
        if platform.system() == 'Darwin':
            logger.info('Dumping classes')
            if bin_type == 'Swift':
                logger.info('Running class-dump-swift against binary')
                if (len(settings.CLASSDUMP_SWIFT_BINARY) > 0
                        and is_file_exists(settings.CLASSDUMP_SWIFT_BINARY)):
                    class_dump_bin = settings.CLASSDUMP_SWIFT_BINARY
                else:
                    class_dump_bin = os.path.join(
                        tools_dir, 'class-dump-swift')
            else:
                logger.info('Running class-dump against binary')
                if (len(settings.CLASSDUMP_BINARY) > 0
                        and is_file_exists(settings.CLASSDUMP_BINARY)):
                    class_dump_bin = settings.CLASSDUMP_BINARY
                else:
                    class_dump_bin = os.path.join(tools_dir, 'class-dump')
            # Execute permission check
            if not os.access(class_dump_bin, os.X_OK):
                os.chmod(class_dump_bin, stat.S_IEXEC)
            args = [class_dump_bin, bin_path]
        elif platform.system() == 'Linux':
            logger.info('Running jtool against the binary for dumping classes')
            args = get_otool_out(tools_dir, 'classdump', bin_path, '')
        else:
            # Platform not supported
            logger.warning('class-dump is not supported in this platform')
            return classdump
        with open(os.devnull, 'w') as devnull:
            # timeout to handle possible deadlock from jtool1
            classdump = subprocess.check_output(args,
                                                stderr=devnull,
                                                timeout=30)
        if b'Source: (null)' in classdump and platform.system() == 'Darwin':
            logger.info('Running fail safe class-dump-swift')
            class_dump_bin = os.path.join(
                tools_dir, 'class-dump-swift')
            args = [class_dump_bin, bin_path]
            classdump = subprocess.check_output(args)
        with open(os.path.join(app_dir, 'classdump.txt'), 'wb') as flip:
            flip.write(classdump)
        return classdump
    except Exception:
        logger.error('class-dump/class-dump-swift failed on this binary')
    return classdump

def strings_on_ipa(bin_path):
    """Extract Strings from IPA."""
    try:
        logger.info('Running strings against the Binary')
        unique_str = []
        unique_str = list(set(strings_util(bin_path)))  # Make unique
        return unique_str
    except Exception:
        logger.exception('Running strings against the Binary')


def get_bin_info(bin_file):
    """Get Binary Information."""
    logger.info('Getting Binary Information')
    m = MachO(bin_file)
    for header in m.headers:
        if header.MH_MAGIC == MH_MAGIC_64 or header.MH_MAGIC == MH_CIGAM_64:
            sz = '64-bit'
        else:
            sz = '32-bit'
        arch = CPU_TYPE_NAMES.get(
            header.header.cputype, header.header.cputype)
        subarch = get_cpu_subtype(
            header.header.cputype, header.header.cpusubtype)
        return {'endian': header.endian,
                'bit': sz,
                'arch': arch,
                'subarch': subarch}


def binary_analysis(src, tools_dir, app_dir, executable_name):
    """Binary Analysis of IPA."""
    try:
        binary_analysis_dict = {}
        binary_findings = {}
        logger.info('Starting Binary Analysis')
        dirs = os.listdir(src)
        dot_app_dir = ''
        for dir_ in dirs:
            if dir_.endswith('.app'):
                dot_app_dir = dir_
                break
        # Bin Dir - Dir/Payload/x.app/
        bin_dir = os.path.join(src, dot_app_dir)
        if (executable_name
                and is_file_exists(os.path.join(bin_dir, executable_name))):
            bin_name = executable_name
        else:
            bin_name = dot_app_dir.replace('.app', '')
        # Bin Path - Dir/Payload/x.app/x
        bin_path = os.path.join(bin_dir, bin_name)
        binary_analysis_dict['libs'] = []
        binary_analysis_dict['bin_res'] = {}
        binary_analysis_dict['strings'] = []
        if not is_file_exists(bin_path):
            logger.warning('MobSF Cannot find binary in %s', bin_path)
            logger.warning('Skipping Otool, Classdump and Strings')
        else:
            bin_info = get_bin_info(bin_path)
            object_data = otool_analysis(
                tools_dir,
                bin_name,
                bin_path,
                bin_dir)
            bin_type = detect_bin_type(object_data['libs'])
            cdump = class_dump(tools_dir, bin_path, app_dir, bin_type)
            binary_rule_matcher(
                binary_findings,
                object_data['bindata'] + cdump,
                ipa_rules.IPA_RULES)
            strings_in_ipa = strings_on_ipa(bin_path)
            binary_analysis_dict['libs'] = object_data['libs']
            binary_analysis_dict['bin_res'] = binary_findings
            binary_analysis_dict['strings'] = strings_in_ipa
            binary_analysis_dict['macho'] = bin_info
            binary_analysis_dict['bin_type'] = bin_type
        return binary_analysis_dict
    except Exception:
        logger.exception('iOS Binary Analysis')
