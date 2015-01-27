#!/usr/bin/env python

from __future__ import print_function

"""
This script parses each "meta" file and extracts the
information needed to deduce build and src dependencies.

It works much the same as the original shell script, but is
*much* more efficient.

The parsing work is handled by the class MetaFile.
We only pay attention to a subset of the information in the
"meta" files.  Specifically:

'CWD'	to initialize our notion.

'C'	to track chdir(2) on a per process basis

'R'	files read are what we really care about.
	directories read, provide a clue to resolving
	subsequent relative paths.  That is if we cannot find
	them relative to 'cwd', we check relative to the last
	dir read.

'W'	files opened for write or read-write,
	for filemon V3 and earlier.
        
'E'	files executed.

'L'	files linked

'V'	the filemon version, this record is used as a clue
	that we have reached the interesting bit.

"""

"""
RCSid:
	$Id: meta2deps.py,v 1.17 2014/04/05 22:56:54 sjg Exp $

	Copyright (c) 2011-2013, Juniper Networks, Inc.
	All rights reserved.

	Redistribution and use in source and binary forms, with or without
	modification, are permitted provided that the following conditions 
	are met: 
	1. Redistributions of source code must retain the above copyright
	   notice, this list of conditions and the following disclaimer. 
	2. Redistributions in binary form must reproduce the above copyright
	   notice, this list of conditions and the following disclaimer in the
	   documentation and/or other materials provided with the distribution.  

	THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
	"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
	LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
	A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
	OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
	SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
	LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
	DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
	THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
	(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
	OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE. 
 
"""

import os, re, sys

def getv(dict, key, d=None):
    """Lookup key in dict and return value or the supplied default."""
    if key in dict:
        return dict[key]
    return d

def resolve(path, cwd, last_dir=None, debug=0, debug_out=sys.stderr):
    """
    Return an absolute path, resolving via cwd or last_dir if needed.
    """
    if path.endswith('/.'):
        path = path[0:-2]
    if len(path) > 0 and path[0] == '/':
        return path
    if path == '.':
        return cwd
    if path.startswith('./'):
        return cwd + path[1:]
    if last_dir == cwd:
        last_dir = None
    for d in [last_dir, cwd]:
        if not d:
            continue
        p = '/'.join([d,path])
        if debug > 2:
            print("looking for:", p, end=' ', file=debug_out)
        if not os.path.exists(p):
            if debug > 2:
                print("nope", file=debug_out)
            p = None
            continue
        if debug > 2:
            print("found:", p, file=debug_out)
        return p
    return None

def abspath(path, cwd, last_dir=None, debug=0, debug_out=sys.stderr):
    """
    Return an absolute path, resolving via cwd or last_dir if needed.
    this gets called a lot, so we try to avoid calling realpath
    until we know we have something.
    """
    rpath = resolve(path, cwd, last_dir, debug, debug_out)
    if rpath:
        path = rpath
    if (path.find('./') > 0 or
        path.endswith('/..') or
        os.path.islink(path)):
        return os.path.realpath(path)
    return path

def sort_unique(list, cmp=None, key=None, reverse=False):
    list.sort(cmp, key, reverse)
    nl = []
    le = None
    for e in list:
        if e == le:
            continue
        nl.append(e)
    return nl

def add_trims(x):
    return ['/' + x + '/',
            '/' + x, 
            x + '/',
            x]

class MetaFile:
    """class to parse meta files generated by bmake."""

    conf = None
    dirdep_re = None
    host_target = None
    srctops = []
    objroots = []
    
    seen = {}
    obj_deps = []
    src_deps = []
    file_deps = []
    
    def __init__(self, name, conf={}):
        """if name is set we will parse it now.
        conf can have the follwing keys:

        SRCTOPS	list of tops of the src tree(s).

        CURDIR	the src directory 'bmake' was run from.

        RELDIR	the relative path from SRCTOP to CURDIR

        MACHINE	the machine we built for.
        	set to 'none' if we are not cross-building.
		More specifically if machine cannot be deduced from objdirs.

        TARGET_SPEC
        	Sometimes MACHINE isn't enough.
                
        HOST_TARGET
		when we build for the pseudo machine 'host'
		the object tree uses HOST_TARGET rather than MACHINE.

        OBJROOTS a list of the common prefix for all obj dirs it might
		end in '/' or '-'.

        DPDEPS	names an optional file to which per file dependencies
		will be appended.
		For example if 'some/path/foo.h' is read from SRCTOP
		then 'DPDEPS_some/path/foo.h +=' "RELDIR" is output.
		This can allow 'bmake' to learn all the dirs within
 		the tree that depend on 'foo.h'

        debug	desired debug level

        debug_out open file to send debug output to (sys.stderr)

        """
        
        self.name = name
        self.debug = getv(conf, 'debug', 0)
        self.debug_out = getv(conf, 'debug_out', sys.stderr)

        self.machine = getv(conf, 'MACHINE', '')
        self.machine_arch = getv(conf, 'MACHINE_ARCH', '')
        self.target_spec = getv(conf, 'TARGET_SPEC', '')
        self.curdir = getv(conf, 'CURDIR')
        self.reldir = getv(conf, 'RELDIR')
        self.dpdeps = getv(conf, 'DPDEPS')
        self.line = 0

        if not self.conf:
            # some of the steps below we want to do only once
            self.conf = conf
            self.host_target = getv(conf, 'HOST_TARGET')
            for srctop in getv(conf, 'SRCTOPS', []):
                if srctop[-1] != '/':
                    srctop += '/'
                if not srctop in self.srctops:
                    self.srctops.append(srctop)
                _srctop = os.path.realpath(srctop)
                if _srctop[-1] != '/':
                    _srctop += '/'
                if not _srctop in self.srctops:
                    self.srctops.append(_srctop)

            trim_list = add_trims(self.machine)
            if self.machine == 'host':
                trim_list += add_trims(self.host_target)
            if self.target_spec:
                trim_list += add_trims(self.target_spec)

            for objroot in getv(conf, 'OBJROOTS', []):
                for e in trim_list:
                    if objroot.endswith(e):
                        # this is not what we want - fix it
                        objroot = objroot[0:-len(e)]
                        if e.endswith('/'):
                            objroot += '/'
                if not objroot in self.objroots:
                    self.objroots.append(objroot)
                    _objroot = os.path.realpath(objroot)
                    if objroot[-1] == '/':
                        _objroot += '/'
                    if not _objroot in self.objroots:
                        self.objroots.append(_objroot)

            # we want the longest match
            self.srctops.sort(reverse=True)
            self.objroots.sort(reverse=True)
            
            if self.debug:
                print("host_target=", self.host_target, file=self.debug_out)
                print("srctops=", self.srctops, file=self.debug_out)
                print("objroots=", self.objroots, file=self.debug_out)

            self.dirdep_re = re.compile(r'([^/]+)/(.+)')

        if self.dpdeps and not self.reldir:
            if self.debug:
                print("need reldir:", end=' ', file=self.debug_out)
            if self.curdir:
                srctop = self.find_top(self.curdir, self.srctops)
                if srctop:
                    self.reldir = self.curdir.replace(srctop,'')
                    if self.debug:
                        print(self.reldir, file=self.debug_out)
            if not self.reldir:
                self.dpdeps = None      # we cannot do it?

        self.cwd = os.getcwd()          # make sure this is initialized

        if name:
            self.try_parse()

    def reset(self):
        """reset state if we are being passed meta files from multiple directories."""
        self.seen = {}
        self.obj_deps = []
        self.src_deps = []
        self.file_deps = []
          
    def dirdeps(self, sep='\n'):
        """return DIRDEPS"""
        return sep.strip() + sep.join(self.obj_deps)
    
    def src_dirdeps(self, sep='\n'):
        """return SRC_DIRDEPS"""
        return sep.strip() + sep.join(self.src_deps)

    def file_depends(self, out=None):
        """Append DPDEPS_${file} += ${RELDIR}
        for each file we saw, to the output file."""
        if not self.reldir:
            return None
        for f in sort_unique(self.file_deps):
            print('DPDEPS_%s += %s' % (f, self.reldir), file=out)

    def seenit(self, dir):
        """rememer that we have seen dir."""
        self.seen[dir] = 1
          
    def add(self, list, data, clue=''):
        """add data to list if it isn't already there."""
        if data not in list:
            list.append(data)
            if self.debug:
                print("%s: %sAdd: %s" % (self.name, clue, data), file=self.debug_out)

    def find_top(self, path, list):
        """the logical tree may be split across multiple trees"""
        for top in list:
            if path.startswith(top):
                if self.debug > 2:
                    print("found in", top, file=self.debug_out)
                return top
        return None

    def find_obj(self, objroot, dir, path, input):
        """return path within objroot, taking care of .dirdep files"""
        ddep = None
        for ddepf in [path + '.dirdep', dir + '/.dirdep']:
            if not ddep and os.path.exists(ddepf):
                ddep = open(ddepf, 'r').readline().strip('# \n')
                if self.debug > 1:
                    print("found %s: %s\n" % (ddepf, ddep), file=self.debug_out)
                if ddep.endswith(self.machine):
                    ddep = ddep[0:-(1+len(self.machine))]
                elif self.target_spec and ddep.endswith(self.target_spec):
                    ddep = ddep[0:-(1+len(self.target_spec))]

        if not ddep:
            # no .dirdeps, so remember that we've seen the raw input
            self.seenit(input)
            self.seenit(dir)
            if self.machine == 'none':
                if dir.startswith(objroot):
                    return dir.replace(objroot,'')
                return None
            m = self.dirdep_re.match(dir.replace(objroot,''))
            if m:
                ddep = m.group(2)
                dmachine = m.group(1)
                if dmachine != self.machine:
                    if not (self.machine == 'host' and
                            dmachine == self.host_target):
                        if self.debug > 2:
                            print("adding .%s to %s" % (dmachine, ddep), file=self.debug_out)
                        ddep += '.' + dmachine

        return ddep

    def try_parse(self, name=None, file=None):
        """give file and line number causing exception"""
        try:
            self.parse(name, file)
        except:
            # give a useful clue
            print('{}:{}: '.format(self.name, self.line), end=' ', file=sys.stderr)
            raise
        
    def parse(self, name=None, file=None):
        """A meta file looks like:
        
	# Meta data file "path"
	CMD "command-line"
	CWD "cwd"
	TARGET "target"
	-- command output --
	-- filemon acquired metadata --
	# buildmon version 3
	V 3
	C "pid" "cwd"
	E "pid" "path"
        F "pid" "child"
	R "pid" "path"
	W "pid" "path"
	X "pid" "status"
        D "pid" "path"
        L "pid" "src" "target"
        M "pid" "old" "new"
        S "pid" "path"
        # Bye bye

        We go to some effort to avoid processing a dependency more than once.
        Of the above record types only C,E,F,L,R,V and W are of interest.
        """

        version = 0                     # unknown
        if name:
            self.name = name;
        if file:
            f = file
            cwd = last_dir = self.cwd
        else:
            f = open(self.name, 'r')
        skip = True
        pid_cwd = {}
        pid_last_dir = {}
        last_pid = 0

        self.line = 0
        if self.curdir:
            self.seenit(self.curdir)    # we ignore this

        interesting = 'CEFLRV'
        for line in f:
            self.line += 1
            # ignore anything we don't care about
            if not line[0] in interesting:
                continue
            if self.debug > 2:
                print("input:", line, end=' ', file=self.debug_out)
            w = line.split()

            if skip:
                if w[0] == 'V':
                    skip = False
                    version = int(w[1])
                    """
                    if version < 4:
                        # we cannot ignore 'W' records
                        # as they may be 'rw'
                        interesting += 'W'
                    """
                elif w[0] == 'CWD':
                    self.cwd = cwd = last_dir = w[1]
                    self.seenit(cwd)    # ignore this
                    if self.debug:
                        print("%s: CWD=%s" % (self.name, cwd), file=self.debug_out)
                continue

            pid = int(w[1])
            if pid != last_pid:
                if last_pid:
                    pid_cwd[last_pid] = cwd
                    pid_last_dir[last_pid] = last_dir
                cwd = getv(pid_cwd, pid, self.cwd)
                last_dir = getv(pid_last_dir, pid, self.cwd)
                last_pid = pid

            # process operations
            if w[0] == 'F':
                npid = int(w[2])
                pid_cwd[npid] = cwd
                pid_last_dir[npid] = cwd
                last_pid = npid
                continue
            elif w[0] == 'C':
                cwd = abspath(w[2], cwd, None, self.debug, self.debug_out)
                if cwd.endswith('/.'):
                    cwd = cwd[0:-2]
                last_dir = cwd
                if self.debug > 1:
                    print("cwd=", cwd, file=self.debug_out)
                continue

            if w[2] in self.seen:
                if self.debug > 2:
                    print("seen:", w[2], file=self.debug_out)
                continue
            # file operations
            if w[0] in 'ML':
                path = w[2].strip("'")
            else:
                path = w[2]
            # we are never interested in .dirdep files as dependencies
            if path.endswith('.dirdep'):
                continue
            # we don't want to resolve the last component if it is
            # a symlink
            path = resolve(path, cwd, last_dir, self.debug, self.debug_out)
            if not path:
                continue
            dir,base = os.path.split(path)
            if dir in self.seen:
                if self.debug > 2:
                    print("seen:", dir, file=self.debug_out)
                continue
            # we can have a path in an objdir which is a link
            # to the src dir, we may need to add dependencies for each
            rdir = dir
            dir = abspath(dir, cwd, last_dir, self.debug, self.debug_out)
            if rdir == dir or rdir.find('./') > 0:
                rdir = None
            # now put path back together
            path = '/'.join([dir,base])
            if self.debug > 1:
                print("raw=%s rdir=%s dir=%s path=%s" % (w[2], rdir, dir, path), file=self.debug_out)
            if w[0] in 'SRWL':
                if w[0] == 'W' and path.endswith('.dirdep'):
                    continue
                if path in [last_dir, cwd, self.cwd, self.curdir]:
                    if self.debug > 1:
                        print("skipping:", path, file=self.debug_out)
                    continue
                if os.path.isdir(path):
                    if w[0] in 'RW':
                        last_dir = path;
                    if self.debug > 1:
                        print("ldir=", last_dir, file=self.debug_out)
                    continue

            if w[0] in 'REWML':
                # finally, we get down to it
                if dir == self.cwd or dir == self.curdir:
                    continue
                srctop = self.find_top(path, self.srctops)
                if srctop:
                    if self.dpdeps:
                        self.add(self.file_deps, path.replace(srctop,''), 'file')
                    self.add(self.src_deps, dir.replace(srctop,''), 'src')
                    self.seenit(w[2])
                    self.seenit(dir)
                    if rdir and not rdir.startswith(srctop):
                        dir = rdir      # for below
                        rdir = None
                    else:
                        continue

                objroot = None
                for dir in [dir,rdir]:
                    if not dir:
                        continue
                    objroot = self.find_top(dir, self.objroots)
                    if objroot:
                        break
                if objroot:
                    ddep = self.find_obj(objroot, dir, path, w[2])
                    if ddep:
                        self.add(self.obj_deps, ddep, 'obj')
                else:
                    # don't waste time looking again
                    self.seenit(w[2])
                    self.seenit(dir)
        if not file:
            f.close()

                            
def main(argv, klass=MetaFile, xopts='', xoptf=None):
    """Simple driver for class MetaFile.

    Usage:
    	script [options] [key=value ...] "meta" ...
        
    Options and key=value pairs contribute to the
    dictionary passed to MetaFile.

    -S "SRCTOP"
		add "SRCTOP" to the "SRCTOPS" list.

    -C "CURDIR"
    
    -O "OBJROOT"
    		add "OBJROOT" to the "OBJROOTS" list.

    -m "MACHINE"

    -a "MACHINE_ARCH"

    -H "HOST_TARGET"

    -D "DPDEPS"
    
    -d	bumps debug level

    """
    import getopt

    # import Psyco if we can
    # it can speed things up quite a bit
    have_psyco = 0
    try:
        import psyco
        psyco.full()
        have_psyco = 1
    except:
        pass

    conf = {
        'SRCTOPS': [],
        'OBJROOTS': [],
        }

    try:
        machine = os.environ['MACHINE']
        if machine:
            conf['MACHINE'] = machine
        machine_arch = os.environ['MACHINE_ARCH']
        if machine_arch:
            conf['MACHINE_ARCH'] = machine_arch
        srctop = os.environ['SB_SRC']
        if srctop:
            conf['SRCTOPS'].append(srctop)
        objroot = os.environ['SB_OBJROOT']
        if objroot:
            conf['OBJROOTS'].append(objroot)
    except:
        pass

    debug = 0
    output = True
    
    opts, args = getopt.getopt(argv[1:], 'a:dS:C:O:R:m:D:H:qT:' + xopts)
    for o, a in opts:
        if o == '-a':
            conf['MACHINE_ARCH'] = a
        elif o == '-d':
            debug += 1
        elif o == '-q':
            output = False
        elif o == '-H':
            conf['HOST_TARGET'] = a
        elif o == '-S':
            if a not in conf['SRCTOPS']:
                conf['SRCTOPS'].append(a)
        elif o == '-C':
            conf['CURDIR'] = a
        elif o == '-O':
            if a not in conf['OBJROOTS']:
                conf['OBJROOTS'].append(a)
        elif o == '-R':
            conf['RELDIR'] = a
        elif o == '-D':
            conf['DPDEPS'] = a
        elif o == '-m':
            conf['MACHINE'] = a
        elif o == '-T':
            conf['TARGET_SPEC'] = a
        elif xoptf:
            xoptf(o, a, conf)

    conf['debug'] = debug

    # get any var=val assignments
    eaten = []
    for a in args:
        if a.find('=') > 0:
            k,v = a.split('=')
            if k in ['SRCTOP','OBJROOT','SRCTOPS','OBJROOTS']:
                if k == 'SRCTOP':
                    k = 'SRCTOPS'
                elif k == 'OBJROOT':
                    k = 'OBJROOTS'
                if v not in conf[k]:
                    conf[k].append(v)
            else:
                conf[k] = v
            eaten.append(a)
            continue
        break

    for a in eaten:
        args.remove(a)

    debug_out = getv(conf, 'debug_out', sys.stderr)

    if debug:
        print("config:", file=debug_out)
        print("psyco=", have_psyco, file=debug_out)
        for k,v in list(conf.items()):
            print("%s=%s" % (k,v), file=debug_out)

    for a in args:
        if a.endswith('.meta'):
            m = klass(a, conf)
        elif a.startswith('@'):
            # there can actually multiple files per line
            for line in open(a[1:]):
                for f in line.strip().split():
                    m = klass(f, conf)

    if output:
        print(m.dirdeps())

        print(m.src_dirdeps('\nsrc:'))

        dpdeps = getv(conf, 'DPDEPS')
        if dpdeps:
            m.file_depends(open(dpdeps, 'wb'))

    return m

if __name__ == '__main__':
    try:
        main(sys.argv)
    except:
        # yes, this goes to stdout
        print("ERROR: ", sys.exc_info()[1])
        raise

