import time
from threading import Thread
import re
import os

class Container:
    constre = re.compile(r'\bconst|virtual|inline|static\b')

    def __init__(self, name, type, parent, info=None):
        self.name = name
        self.type = type
        self.parent = parent
        self.info = info

        # These are all the children of this container. These are
        # containers themselves. We use a dictionary here to map the
        # "names" of the children to the actual objects. Note that multiple
        # children can share the same name (in case of function
        # overloading). Therefore, the value of this dictionary is a list
        # containing all objects which share the name.
        self.children = {}

    def isFcnType(self, type):
        return (type in 'mfp')

    def parseFile(self, fileName):
        # Note that we depend on the tags being generated using the command
        #   > ctags -R --fields=+iaS testtags.cpp

        taglines = open(fileName).readlines()

        # Refuse to deal with non ctags generated files.
        if not taglines[0].startswith('!_TAG_FILE_FORMAT\t2'):
            return

        # skip to the first non-comment line
        firstTagLine = 0
        while taglines[firstTagLine][0] == '!':
            firstTagLine += 1

        for line in taglines[firstTagLine:]:
            mandatory, optional = line.split(';"\t')

            tag, file, location = mandatory.split("\t", 2)
            otherFields = optional.split("\t",)
            type = otherFields[0]
            
            # build a dictionary from all other information present in the tags
            # file.
            info = otherFields[1:]
            infoDict = {}
            for infoItem in info:
                key, value = infoItem.split(':', 1)
                infoDict[key] = value.strip()

            infoDict['file'] = file
            infoDict['location'] = location

            if self.isFcnType(type):
                location = Container.constre.sub('', location)
                returnType = location[2:].split(None,1)[0]
                if returnType[-1] == '*':
                    returnType = returnType[:-1]
                infoDict['returnType'] = returnType
            else:
                infoDict['returnType'] = None

            if 'class' in infoDict:
                path = infoDict['class'].split('::')
            elif 'namespace' in infoDict:
                path = infoDict['namespace'].split('::')
            elif 'enum' in infoDict:
                path = infoDict['enum'].split('::')
            elif 'struct' in infoDict:
                path = infoDict['struct'].split('::')
            elif 'union' in infoDict:
                path = infoDict['union'].split('::')
            else:
                path = []

            self.add(tag, type, path, infoDict)
            # for enums, C/C++ have completely flat scoping. This is
            # probably not right with enums declared in namespaces, but
            # that will require way too much cleverness.
            if type == 'e':
                self.add(tag, type, [], infoDict)

        self.parsingComplete = 1

    def add(self, name, type, pathList, info=None):
        if not pathList:
            if not name in self.children:
                # print 'new: info = ', info
                self.children[name] = [Container(name, type, self, info=info)]
            else:
                # This part is to handle function overloading. Basically,
                # if we have found a function and there is already a
                # function defined with the same name, then we want to add
                # a new member
                # print 'old: info = ', info
                if self.children[name][-1].type is not None:
                    self.children[name].append(Container(name, type, self,
                                                         info=info))
                else:
                    self.children[name][-1].type = type
                    self.children[name][-1].info = info
        else:
            if not pathList[0] in self.children:
                # as of now, we do not know the type or information about
                # this object. Later, when we come across the object
                # definition, these fields will be set.
                self.children[pathList[0]] = [Container(pathList[0], None, self, info=None)]

            # now push this object downstream.
            self.children[pathList[0]][0].add(name, type, pathList[1:], info=info)

    def getObject(self, path, activeNamespaces=[]):
        """
        @path:
            the path to the container which we want to find.
            Ex:
                ['CG', 'AddExpr']
                This corresponds to something like CG::AddExpr
        @activeNamespaces:
            The namespaces which are active at this point. This is another list
            of lists arising from `using namespace` declarations.
            Ex:
                [['ns1'], ['ns2', 'ns3']]
                from
                using ns1;
                using ns2::ns3;
        """
        for nameSpace in [[]] + activeNamespaces:
            retval = self._getObject(nameSpace + path, self, activeNamespaces)
            if retval:
                return retval.resolveTypedefs(self, activeNamespaces)

        return None

    def _getObject(self, path, root, activeNamespaces):
        # print '+_getObject: self = %s, path = %s' % (self.name, path)
        if not path:
            # print '-_getObject: returning self'
            return self
        else:
            # print ":_getObject: search from %s for %s" % (self.name, path[0])
            container = self.searchUpwardsThroughClass(path[0], root, activeNamespaces)
            if container:
                child = container.children[path[0]][0]
                child = child.resolveTypedefs(root, activeNamespaces)
                if child:
                    return child._getObject(path[1:], root, activeNamespaces)
                else:
                    return None
            else:
                # print '-_getObject: returning None'
                return None

    def getMemberList(self, path, activeNamespaces):
        container = self.getObject(path, activeNamespaces)
        if not container:
            return []
        list = container._getMemberList(self, activeNamespaces)

        flatten(list)
        decoList = [(item.name, i, item) for i, item in enumerate(list)]
        decoList.sort()
        finalList = [item for _, _,item in decoList]

        return finalList

    def _getMemberList(self, root, activeNamespaces=[]):
        # print 'self.name = %s, self.type = %s, self = %s' % (self.name,
        #                                                      self.type,
        #                                                      self.__dict__)
        type = self.resolveTypedefs(root, activeNamespaces)

        retval = type.children.values()
        if type.type == 'c' and self.info:
            if 'inherits' in type.info:
                parentName = type.info['inherits'].split('::')
                # NOTE: We need to add the namespace of this object itself
                parentObj = root.getObject(parentName,
                                           activeNamespaces=activeNamespaces)
                if parentObj:
                    retval += parentObj._getMemberList(root,
                                                      activeNamespaces=activeNamespaces)

        return retval

    def searchUpwardsThroughClass(self, name, root, activeNamespaces):
        # print '+searchUpwardsThroughClass'
        if name in self.children:
            return self
        elif self.type == 'c' and self.info and 'inherits' in self.info:
            parentName = self.info['inherits'].split('::')
            # NOTE: We need to add the namespace of this object itself
            parentObj = root.getObject(parentName, activeNamespaces)
            if parentObj:
                return parentObj.searchUpwardsThroughClass(name, root, activeNamespaces)
        else:
            return None

    def resolveTypedefs(self, root, activeNamespaces):
        # print '+resolveTypedefs: self = %s' % self.name
        if self.info and 'typeref' in self.info:
            # print 'in typeref'
            # A typeref is of the form:
            #       typeref:struct:CG::AddExpr
            typeref = self.info['typeref'].split(':', 1)[1]

            # This is the path to which this self is typeref'ed to.
            # In the above example ['CG', 'AddExpr']
            typerefpath = typeref.split('::')

            return root.getObject(typerefpath, activeNamespaces)

        elif self.isFcnType(self.type):
            if not self.info:
                return None

            returnType = self.info['returnType']
            return root.getObject(returnType.split('::'), activeNamespaces)

        else:
            return self

    def toString(self, prefix=''):
        ret = '%s%s (%s) [%s]\n' % (prefix, self.name, self.type, self.info)
        for name, children in self.children.items():
            for ch in children:
                ret += ch.toString(prefix=prefix+'  ')
        return ret

    def __str__(self):
        return self.toString()

class ParseTagsFileThread (Thread):
    def __init__(self, globalNamespace, filenames):
        Thread.__init__(self)
        self.tagsFilenames = filenames
        self.globalNamespace = globalNamespace

    def run(self):
        for fname in self.tagsFilenames:
            if not os.path.isfile(fname):
                continue
            self.globalNamespace.parseFile(fname)

# A cool list flattening function from comp.lang.python <<<
import sys
import types
def flatten(inlist, type=type, listtype=types.ListType, \
     integers = xrange(sys.maxint), endoflist=IndexError):
    '''
    Destructively flatten a list hierarchy to a single level.
    Non-recursive, and (as far as I can see, doesn't have any
    glaring loopholes).

    Provided by Tim Peters, Mike Fletcher and Christian Tismer
    on comp.lang.python
    '''
    try:
        for ind in integers :
            while type(inlist[ind]) is listtype:
                inlist[ind:ind+1] = inlist[ind]
    except endoflist:
        return inlist 

# >>>

# only when importing into vim <<<
try:
    import vim
    import traceback
except:
    pass

vimOmniDebug = ''

import re
def vimDebug(str):
    vim.eval('Debug("%s", "omni")' % (str))
    global vimOmniDebug
    vimOmniDebug += '%s\n' % str

class VimTagsCompleter:
    def __init__(self):
        self.root = Container('Head', 'n', None, info=None)
        self.tagsFiles = vim.eval('&tags').split(',')
        self.pthread = ParseTagsFileThread(self.root, self.tagsFiles)
        self.pthread.start()

    def performCompletion(self, prefix):
        self.pthread.join()

        if prefix:
            curpos = vim.current.window.cursor
            lastToken = vim.eval('cpp_omni#GetLastToken()')
            vim.current.window.cursor = curpos
            if lastToken == '->':
                wordChain = vim.eval('cpp_omni#GetWordChain()')
            else:
                wordChain = []
        else:
            wordChain = vim.eval('cpp_omni#GetWordChain()')

        vimDebug('wordChain before mapping = %s' % wordChain)

        firstType = vim.eval('cpp_omni#GetWordType("%s")' % wordChain[0])

        if firstType:
            wordChain[0:1] = firstType

        vimDebug('wordChain after mapping = %s' % wordChain)
        try:
            list = self.root.getMemberList(wordChain, [['CG'], ['SF']])
        except:
            cstr = cStringIO.StringIO()
            traceback.print_exc(file=cstr)
            vimDebug('Exception in getting list:\n%s' % cstr.getvalue())
            return

        if not list:
            return

        vimDebug('trying to complete with %d items, prefix = %r' % (len(list), prefix))
        prefre = re.compile(prefix)
        prevItem = None
        for item in list:
            # The following conditions need to be met for an item to be
            # listed:
            # 1. It should not be a constructor or destructor.
            # 2. It should not be a function already defined in a child
            #    class.
            if (prefre.match(item.name) and 
                item.name != item.parent.name and
                item.name != ('~'+item.parent.name) and
                (prevItem is None or item.name != prevItem.name or 
                 prevItem.parent.name == item.parent.name)):

                if not item.info:
                    continue

                d = {'word': item.name, 'kind': item.type, 
                     'menu': item.parent.name, 
                     'info': item.info['location'],
                     'dup': 1 }
                vim.eval('complete_add(%s)' % d.__repr__())
                prevItem = item

# >>>

def printMemberList(root, path, activens):
    finalList = root.getMemberList(path, activens)

    print 'list of members -------------'
    for m in finalList:
        print m

def main():
    root = Container('Head', 'n', None, info=None)
    tagsFiles = sys.argv[1:]
    if not tagsFiles:
        print 'No tags files found'
        sys.exit(0)

    pthread = ParseTagsFileThread(root, tagsFiles)
    pthread.start()
    pthread.join()

    # print root
    # obj = root.getObject(['std', 'vector', 'iterator'], [['std']])
    # obj = root.getObject(['CG', 'MatrixRefExpr'], [['CG']])
    # print obj

    # printMemberList(root, ['CdrCtxInfo', 'options'], [])
    # printMemberList(root, ['CdrCtxInfo', 'currentModuleInfo'], [])
    printMemberList(root, ['CG', 'CallExpr', 'cast'], [['CG']])
    # printMemberList(root, ['VarExpr'], [['CG']])
    # printMemberList(root, ['CfgLstLoop'], [['CG']])
    # printMemberList(root, ['LstLoopInterface'], [['CG']])
    # printMemberList(root, ['CG', 'CallExpr'], [['CG'], ['SF']])
    # printMemberList(root, ['CG'], [['CG'], ['SF']])
    # printMemberList(root, ['CG', 'Scope'], [['CG']])

    # printMemberList(root, ['DB_chart', 'absTimerEventTemporalCounters', 'array'], [])
    # printMemberList(root, ['TemporalCounterArray'], [])

if __name__ == "__main__":
    main()
