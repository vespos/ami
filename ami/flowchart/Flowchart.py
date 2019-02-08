# -*- coding: utf-8 -*-
from pyqtgraph.Qt import QtCore, QtGui, QT_LIB
from pyqtgraph.pgcollections import OrderedDict
from pyqtgraph import FileDialog
from pyqtgraph.debug import printExc
from pyqtgraph import configfile as configfile
from pyqtgraph import dockarea as dockarea
from pyqtgraph.flowchart import FlowchartGraphicsView
from pyqtgraph import functions as fn
from pyqtgraph import GraphicsObject
from numpy import ndarray
from ami.flowchart.Terminal import Terminal
from ami.flowchart.library import LIBRARY
from ami.flowchart.Node import Node
from ami.comm import GraphCommHandler
from ami.graphkit_wrapper import Graph

import ami.flowchart.FlowchartCtrlTemplate_pyqt5 as FlowchartCtrlTemplate
import os
import threading


class Flowchart(Node):
    sigFileLoaded = QtCore.Signal(object)
    sigFileSaved = QtCore.Signal(object)

    sigChartLoaded = QtCore.Signal()
    # called when output is expected to have changed
    sigStateChanged = QtCore.Signal()
    # called when nodes are added, removed, or renamed.
    sigChartChanged = QtCore.Signal(object, object, object)  # (self, action, node)

    def __init__(self, terminals=None, name=None, filePath=None, library=None, addr=""):
        self.library = library or LIBRARY
        self.addr = addr
        if name is None:
            name = "Flowchart"
        if terminals is None:
            terminals = {}
        self.filePath = filePath
        #  create node without terminals; we'll add these later
        Node.__init__(self, name, allowAddInput=True, allowAddOutput=True)

        self.inputWasSet = False   # flag allows detection of changes in the absence of input change.
        self._nodes = {}
        self.nextZVal = 10
        # self.connects = []
        # self._chartGraphicsItem = FlowchartGraphicsItem(self)
        self._widget = None
        self._scene = None

        self.widget()

        self.inputNode = Node('Input', allowRemove=False, allowAddOutput=True)
        self.addNode(self.inputNode, 'Input', [-150, 0])

        self.inputNode.sigTerminalRenamed.connect(self.internalTerminalRenamed)
        self.inputNode.sigTerminalRemoved.connect(self.internalTerminalRemoved)
        self.inputNode.sigTerminalAdded.connect(self.internalTerminalAdded)

        self.viewBox.autoRange(padding=0.04)

        for name, opts in terminals.items():
            self.addTerminal(name, **opts)

    def setLibrary(self, lib):
        self.library = lib
        self.widget().chartWidget.buildMenu()

    def setInput(self, **args):
        """Set the input values of the flowchart. This will automatically propagate
        the new values throughout the flowchart, (possibly) causing the output to change.
        """
        # print "setInput", args
        # Node.setInput(self, **args)
        # print "  ....."
        self.inputWasSet = True
        self.inputNode.setOutput(**args)

    def nodes(self):
        return self._nodes

    def addTerminal(self, name, **opts):
        term = Node.addTerminal(self, name, **opts)
        name = term.name()
        if opts['io'] == 'in':  # inputs to the flowchart become outputs on the input node
            opts['io'] = 'out'
            opts['multi'] = False
            self.inputNode.sigTerminalAdded.disconnect(self.internalTerminalAdded)
            try:
                self.inputNode.addTerminal(name, **opts)
            finally:
                self.inputNode.sigTerminalAdded.connect(self.internalTerminalAdded)

        return term

    def removeTerminal(self, name):
        # print "remove:", name
        term = self[name]
        inTerm = self.internalTerminal(term)
        Node.removeTerminal(self, name)
        inTerm.node().removeTerminal(inTerm.name())

    def internalTerminalRenamed(self, term, oldName):
        self[oldName].rename(term.name())

    def internalTerminalAdded(self, node, term):
        if term._io == 'in':
            io = 'out'
        else:
            io = 'in'
        Node.addTerminal(self,
                         term.name(),
                         io=io, renamable=term.isRenamable(),
                         removable=term.isRemovable(),
                         multiable=term.isMultiable())

    def internalTerminalRemoved(self, node, term):
        try:
            Node.removeTerminal(self, term.name())
        except KeyError:
            pass

    def terminalRenamed(self, term, oldName):
        newName = term.name()
        # print "flowchart rename", newName, oldName
        # print self.terminals
        Node.terminalRenamed(self, self[oldName], oldName)
        # print self.terminals
        for n in [self.inputNode]:
            if oldName in n.terminals:
                n[oldName].rename(newName)

    def createNode(self, nodeType, name=None, pos=None):
        """Create a new Node and add it to this flowchart.
        """
        if name is None:
            n = 0
            while True:
                name = "%s.%d" % (nodeType, n)
                if name not in self._nodes:
                    break
                n += 1

        # create an instance of the node
        node = self.library.getNodeType(nodeType)(name, addr=self.addr)
        self.addNode(node, name, pos)
        return node

    def addNode(self, node, name, pos=None):
        """Add an existing Node to this flowchart.

        See also: createNode()
        """
        if pos is None:
            pos = [0, 0]
        if type(pos) in [QtCore.QPoint, QtCore.QPointF]:
            pos = [pos.x(), pos.y()]
        item = node.graphicsItem()
        item.setZValue(self.nextZVal*2)
        self.nextZVal += 1
        self.viewBox.addItem(item)
        item.moveBy(*pos)
        self._nodes[name] = node
        if node is not self.inputNode:
            self.widget().addNode(node)
        node.sigClosed.connect(self.nodeClosed)
        node.sigRenamed.connect(self.nodeRenamed)
        self.sigChartChanged.emit(self, 'add', node)

    def removeNode(self, node):
        """Remove a Node from this flowchart.
        """
        node.close()

    def nodeClosed(self, node):
        del self._nodes[node.name()]
        self.widget().removeNode(node)
        for signal in ['sigClosed', 'sigRenamed']:
            try:
                getattr(node, signal).disconnect(self.nodeClosed)
            except (TypeError, RuntimeError):
                pass
        self.sigChartChanged.emit(self, 'remove', node)

    def nodeRenamed(self, node, oldName):
        del self._nodes[oldName]
        self._nodes[node.name()] = node
        self.widget().nodeRenamed(node, oldName)
        self.sigChartChanged.emit(self, 'rename', node)

    def arrangeNodes(self):
        pass

    def internalTerminal(self, term):
        """If the terminal belongs to the external Node, return the corresponding internal terminal"""
        if term.node() is self:
            if term.isInput():
                return self.inputNode[term.name()]
        else:
            return term

    def connectTerminals(self, term1, term2):
        """Connect two terminals together within this flowchart."""
        term1 = self.internalTerminal(term1)
        term2 = self.internalTerminal(term2)
        term1.connectTo(term2)

    def processOrder(self):
        """Return the order of operations required to process this chart.
        The order returned should look like [('p', node1), ('p', node2), ('d', terminal1), ...]
        where each tuple specifies either (p)rocess this node or (d)elete the result from this terminal
        """

        #  first collect list of nodes/terminals and their dependencies
        deps = {}
        tdeps = {}   # {terminal: [nodes that depend on terminal]}
        for name, node in self._nodes.items():
            deps[node] = node.dependentNodes()
            for t in node.outputs().values():
                tdeps[t] = t.dependentNodes()

        # print "DEPS:", deps
        #  determine correct node-processing order
        order = fn.toposort(deps)
        # print "ORDER1:", order

        #  construct list of operations
        ops = [('p', n) for n in order]

        #  determine when it is safe to delete terminal values
        dels = []
        for t, nodes in tdeps.items():
            lastInd = 0
            lastNode = None
            #  determine which node is the last to be processed according to order
            for n in nodes:
                if n is self:
                    lastInd = None
                    break
                else:
                    try:
                        ind = order.index(n)
                    except ValueError:
                        continue
                if lastNode is None or ind > lastInd:
                    lastNode = n
                    lastInd = ind
            if lastInd is not None:
                dels.append((lastInd+1, t))
        dels.sort(key=lambda a: a[0], reverse=True)
        for i, t in dels:
            ops.insert(i, ('d', t))
        return ops

    def chartGraphicsItem(self):
        """Return the graphicsItem that displays the internal nodes and
        connections of this flowchart.

        Note that the similar method `graphicsItem()` is inherited from Node
        and returns the *external* graphical representation of this flowchart."""
        return self.viewBox

    def widget(self):
        """Return the control widget for this flowchart.

        This widget provides GUI access to the parameters for each node and a
        graphical representation of the flowchart.
        """
        if self._widget is None:
            self._widget = FlowchartCtrlWidget(self, self.addr)
            self.scene = self._widget.scene()
            self.viewBox = self._widget.viewBox()
        return self._widget

    def listConnections(self):
        conn = set()
        for n in self._nodes.values():
            terms = n.outputs()
            for n, t in terms.items():
                for c in t.connections():
                    conn.add((t, c))
        return conn

    def saveState(self):
        """Return a serializable data structure representing the current state of this flowchart.
        """
        state = Node.saveState(self)
        state['nodes'] = []
        state['connects'] = []

        for name, node in self._nodes.items():
            cls = type(node)
            if hasattr(cls, 'nodeName'):
                clsName = cls.nodeName
                pos = node.graphicsItem().pos()
                ns = {'class': clsName, 'name': name, 'pos': (pos.x(), pos.y()), 'state': node.saveState()}
                state['nodes'].append(ns)

        conn = self.listConnections()
        for a, b in conn:
            state['connects'].append((a.node().name(), a.name(), b.node().name(), b.name()))

        state['inputNode'] = self.inputNode.saveState()

        return state

    def restoreState(self, state, clear=False):
        """Restore the state of this flowchart from a previous call to `saveState()`.
        """
        self.blockSignals(True)
        try:
            if clear:
                self.clear()
            Node.restoreState(self, state)
            nodes = state['nodes']
            nodes.sort(key=lambda a: a['pos'][0])
            for n in nodes:
                if n['name'] in self._nodes:
                    self._nodes[n['name']].restoreState(n['state'])
                    continue
                try:
                    node = self.createNode(n['class'], name=n['name'])
                    node.restoreState(n['state'])
                except Exception:
                    printExc("Error creating node %s: (continuing anyway)" % n['name'])

            self.inputNode.restoreState(state.get('inputNode', {}))

            # self.restoreTerminals(state['terminals'])
            for n1, t1, n2, t2 in state['connects']:
                try:
                    self.connectTerminals(self._nodes[n1][t1], self._nodes[n2][t2])
                except Exception:
                    print(self._nodes[n1].terminals)
                    print(self._nodes[n2].terminals)
                    printExc("Error connecting terminals %s.%s - %s.%s:" % (n1, t1, n2, t2))

        finally:
            self.blockSignals(False)

        self.sigChartLoaded.emit()
        self.sigStateChanged.emit()

    def loadFile(self, fileName=None, startDir=None):
        """Load a flowchart (*.fc) file.
        """
        if fileName is None:
            if startDir is None:
                startDir = self.filePath
            if startDir is None:
                startDir = '.'
            self.fileDialog = FileDialog(None, "Load Flowchart..", startDir, "Flowchart (*.fc)")
            self.fileDialog.show()
            self.fileDialog.fileSelected.connect(self.loadFile)
            return
            #  NOTE: was previously using a real widget for the file dialog's parent,
            #        but this caused weird mouse event bugs..
        state = configfile.readConfigFile(fileName)
        self.restoreState(state, clear=True)
        self.viewBox.autoRange()
        self.sigFileLoaded.emit(fileName)
        return fileName

    def saveFile(self, fileName=None, startDir=None, suggestedFileName='flowchart.fc'):
        """Save this flowchart to a .fc file
        """
        if fileName is None:
            if startDir is None:
                startDir = self.filePath
            if startDir is None:
                startDir = '.'
            self.fileDialog = FileDialog(None, "Save Flowchart..", startDir, "Flowchart (*.fc)")
            self.fileDialog.setAcceptMode(QtGui.QFileDialog.AcceptSave)
            self.fileDialog.show()
            self.fileDialog.fileSelected.connect(self.saveFile)
            return
        configfile.writeConfigFile(self.saveState(), fileName)
        self.sigFileSaved.emit(fileName)

    def clear(self):
        """Remove all nodes from this flowchart except the original input/output nodes.
        """
        for n in list(self._nodes.values()):
            if n is self.inputNode:
                continue
            n.close()  # calls self.nodeClosed(n) by signal
        # self.clearTerminals()
        self.widget().clear()

    def clearTerminals(self):
        Node.clearTerminals(self)
        self.inputNode.clearTerminals()


class FlowchartGraphicsItem(GraphicsObject):

    def __init__(self, chart):
        GraphicsObject.__init__(self)
        self.chart = chart  # chart is an instance of Flowchart()
        self.updateTerminals()

    def updateTerminals(self):
        self.terminals = {}
        bounds = self.boundingRect()
        inp = self.chart.inputs()
        dy = bounds.height() / (len(inp)+1)
        y = dy
        for n, t in inp.items():
            item = t.graphicsItem()
            self.terminals[n] = item
            item.setParentItem(self)
            item.setAnchor(bounds.width(), y)
            y += dy
        out = self.chart.outputs()
        dy = bounds.height() / (len(out)+1)
        y = dy
        for n, t in out.items():
            item = t.graphicsItem()
            self.terminals[n] = item
            item.setParentItem(self)
            item.setAnchor(0, y)
            y += dy

    def boundingRect(self):
        # print "FlowchartGraphicsItem.boundingRect"
        return QtCore.QRectF()

    def paint(self, p, *args):
        # print "FlowchartGraphicsItem.paint"
        pass
        # p.drawRect(self.boundingRect())


class FlowchartCtrlWidget(QtGui.QWidget):
    """
    The widget that contains the list of all the nodes in a flowchart and their controls,
    as well as buttons for loading/saving flowcharts.
    """

    def __init__(self, chart, addr):
        self.items = {}
        # self.loadDir = loadDir  #  where to look initially for chart files
        self.currentFileName = None
        QtGui.QWidget.__init__(self)
        self.chart = chart
        self.ui = FlowchartCtrlTemplate.Ui_Form()
        self.ui.setupUi(self)
        self.ui.ctrlList.setColumnCount(2)
        # self.ui.ctrlList.setColumnWidth(0, 200)
        self.ui.ctrlList.setColumnWidth(1, 20)
        self.ui.ctrlList.setVerticalScrollMode(self.ui.ctrlList.ScrollPerPixel)
        self.ui.ctrlList.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        self.pending = {}
        self.features = {}
        self.pending_lock = threading.Lock()

        self.graphCommHandler = GraphCommHandler(addr)
        self.chartWidget = FlowchartWidget(chart, self)

        h = self.ui.ctrlList.header()
        if QT_LIB in ['PyQt4', 'PySide']:
            h.setResizeMode(0, h.Stretch)
        else:
            h.setSectionResizeMode(0, h.Stretch)

        self.ui.ctrlList.itemChanged.connect(self.itemChanged)
        self.ui.loadBtn.clicked.connect(self.loadClicked)
        self.ui.saveBtn.clicked.connect(self.saveClicked)
        self.ui.saveAsBtn.clicked.connect(self.saveAsClicked)
        self.ui.applyBtn.clicked.connect(self.apply)
        self.chart.sigFileLoaded.connect(self.setCurrentFile)
        self.ui.reloadBtn.clicked.connect(self.reloadClicked)
        self.chart.sigFileSaved.connect(self.fileSaved)

    def apply(self):
        nodes = self.chart.nodes()
        graph_nodes = []
        for name, node in nodes.items():
            if hasattr(node, 'to_operation'):
                n = node.to_operation()
                if type(n) is list:
                    graph_nodes.extend(n)
                else:
                    graph_nodes.append(n)
        graph = Graph(name=str(self.chart.name))
        graph.add(graph_nodes)
        self.graphCommHandler.update(graph)
        self.features = {}
        self.pending = {}

    def reloadClicked(self):
        try:
            self.chartWidget.reloadLibrary()
            self.ui.reloadBtn.success("Reloaded.")
        except Exception:
            self.ui.reloadBtn.success("Error.")
            raise

    def loadClicked(self):
        newFile = self.chart.loadFile()
        self.setCurrentFile(newFile)

    def fileSaved(self, fileName):
        self.setCurrentFile(fileName)
        self.ui.saveBtn.success("Saved.")

    def saveClicked(self):
        if self.currentFileName is None:
            self.saveAsClicked()
        else:
            try:
                self.chart.saveFile(self.currentFileName)
                # self.ui.saveBtn.success("Saved.")
            except Exception as e:
                self.ui.saveBtn.failure("Error")
                raise e

    def saveAsClicked(self):
        try:
            if self.currentFileName is None:
                self.chart.saveFile()
            else:
                self.chart.saveFile(suggestedFileName=self.currentFileName)
            # self.ui.saveAsBtn.success("Saved.")
            # print "Back to saveAsClicked."
        except Exception as e:
            self.ui.saveBtn.failure("Error")
            raise e

        # self.setCurrentFile(newFile)

    def setCurrentFile(self, fileName):
        self.currentFileName = fileName
        if fileName is None:
            self.ui.fileNameLabel.setText("<b>[ new ]</b>")
        else:
            self.ui.fileNameLabel.setText("<b>%s</b>" % os.path.split(self.currentFileName)[1])
        self.resizeEvent(None)

    def itemChanged(self, *args):
        pass

    def scene(self):
        # returns the GraphicsScene object
        return self.chartWidget.scene()

    def viewBox(self):
        return self.chartWidget.viewBox()

    def nodeRenamed(self, node, oldName):
        self.items[node].setText(0, node.name())

    def addNode(self, node):
        ctrl = node.ctrlWidget()
        # if ctrl is None:
        #     return
        item = QtGui.QTreeWidgetItem([node.name(), '', ''])
        self.ui.ctrlList.addTopLevelItem(item)

        if ctrl is not None:
            item2 = QtGui.QTreeWidgetItem()
            item.addChild(item2)
            self.ui.ctrlList.setItemWidget(item2, 0, ctrl)

        self.items[node] = item

    def removeNode(self, node):
        if node in self.items:
            item = self.items[node]
            # self.disconnect(item.bypassBtn, QtCore.SIGNAL('clicked()'), self.bypassClicked)
            self.ui.ctrlList.removeTopLevelItem(item)

    def chartWidget(self):
        return self.chartWidget

    def clear(self):
        self.chartWidget.clear()

    def select(self, node):
        item = self.items[node]
        self.ui.ctrlList.setCurrentItem(item)


class FlowchartWidget(dockarea.DockArea):
    """Includes the actual graphical flowchart and debugging interface"""
    def __init__(self, chart, ctrl):
        # QtGui.QWidget.__init__(self)
        dockarea.DockArea.__init__(self)
        self.chart = chart
        self.ctrl = ctrl
        self.hoverItem = None
        # self.setMinimumWidth(250)
        # self.setSizePolicy(QtGui.QSizePolicy(QtGui.QSizePolicy.Preferred, QtGui.QSizePolicy.Expanding))

        # self.ui = FlowchartTemplate.Ui_Form()
        # self.ui.setupUi(self)

        #  build user interface (it was easier to do it here than via developer)
        self.view = FlowchartGraphicsView.FlowchartGraphicsView(self)
        self.viewDock = dockarea.Dock('view', size=(1000, 600))
        self.viewDock.addWidget(self.view)
        self.viewDock.hideTitleBar()
        self.addDock(self.viewDock)

        self.hoverText = QtGui.QTextEdit()
        self.hoverText.setReadOnly(True)
        self.hoverDock = dockarea.Dock('Hover Info', size=(1000, 20))
        self.hoverDock.addWidget(self.hoverText)
        self.addDock(self.hoverDock, 'bottom')

        self._scene = self.view.scene()
        self._viewBox = self.view.viewBox()
        # self._scene = QtGui.QGraphicsScene()
        # self._scene = FlowchartGraphicsView.FlowchartGraphicsScene()
        # self.view.setScene(self._scene)

        self.buildMenu()
        # self.ui.addNodeBtn.mouseReleaseEvent = self.addNodeBtnReleased

        self._scene.selectionChanged.connect(self.selectionChanged)
        self._scene.sigMouseHover.connect(self.hoverOver)
        # self.view.sigClicked.connect(self.showViewMenu)
        # self._scene.sigSceneContextMenu.connect(self.showViewMenu)
        # self._viewBox.sigActionPositionChanged.connect(self.menuPosChanged)

    def reloadLibrary(self):
        # QtCore.QObject.disconnect(self.nodeMenu, QtCore.SIGNAL('triggered(QAction*)'), self.nodeMenuTriggered)
        self.nodeMenu.triggered.disconnect(self.nodeMenuTriggered)
        self.nodeMenu = None
        self.subMenus = []
        self.chart.library.reload()
        self.buildMenu()

    def buildMenu(self, pos=None):
        def buildSubMenu(node, rootMenu, subMenus, pos=None):
            for section, node in node.items():
                menu = QtGui.QMenu(section)
                rootMenu.addMenu(menu)
                if isinstance(node, OrderedDict):
                    buildSubMenu(node, menu, subMenus, pos=pos)
                    subMenus.append(menu)
                else:
                    act = rootMenu.addAction(section)
                    act.nodeType = section
                    act.pos = pos
        self.nodeMenu = QtGui.QMenu()
        self.subMenus = []
        buildSubMenu(self.chart.library.getNodeTree(), self.nodeMenu, self.subMenus, pos=pos)
        self.nodeMenu.triggered.connect(self.nodeMenuTriggered)
        return self.nodeMenu

    def menuPosChanged(self, pos):
        self.menuPos = pos

    def showViewMenu(self, ev):
        # QtGui.QPushButton.mouseReleaseEvent(self.ui.addNodeBtn, ev)
        # if ev.button() == QtCore.Qt.RightButton:
            # self.menuPos = self.view.mapToScene(ev.pos())
            # self.nodeMenu.popup(ev.globalPos())
        # print "Flowchart.showViewMenu called"

        # self.menuPos = ev.scenePos()
        self.buildMenu(ev.scenePos())
        self.nodeMenu.popup(ev.screenPos())

    def scene(self):
        return self._scene  # the GraphicsScene item

    def viewBox(self):
        return self._viewBox  # the viewBox that items should be added to

    def nodeMenuTriggered(self, action):
        nodeType = action.nodeType
        if action.pos is not None:
            pos = action.pos
        else:
            pos = self.menuPos
        pos = self.viewBox().mapSceneToView(pos)

        self.chart.createNode(nodeType, pos=pos)

    def selectionChanged(self):
        # print "FlowchartWidget.selectionChanged called."
        items = self._scene.selectedItems()

        if len(items) != 1:
            return

        item = items[0]
        if hasattr(item, 'node') and isinstance(item.node, Node):
            n = item.node

            for k, term in n.terminals.items():
                inputs = term.inputTerminals()

                for i in inputs:
                    if i.node().name() == "Input":
                        name = i.name()
                    else:
                        name = i.node().name()

                    if name in self.ctrl.features:
                        topic = name
                    else:
                        topic = self.ctrl.graphCommHandler.auto(name)

                    request_view = False
                    with self.ctrl.pending_lock:
                        if topic not in self.ctrl.pending:
                            self.ctrl.pending[topic] = name
                            request_view = True
                    if request_view:
                        self.ctrl.graphCommHandler.view(name)
                    n.display(name, topic)

            self.ctrl.select(n)

    def hoverOver(self, items):
        # print "FlowchartWidget.hoverOver called."
        term = None
        for item in items:
            if item is self.hoverItem:
                return
            self.hoverItem = item
            if hasattr(item, 'term') and isinstance(item.term, Terminal):
                term = item.term
                break
        if term is None:
            self.hoverText.setPlainText("")
        else:
            val = term.value()
            if isinstance(val, ndarray):
                val = "%s %s %s" % (type(val).__name__, str(val.shape), str(val.dtype))
            else:
                val = str(val)
                if len(val) > 400:
                    val = val[:400] + "..."
            self.hoverText.setPlainText("%s.%s = %s" % (term.node().name(), term.name(), val))
            # self.hoverLabel.setCursorPosition(0)

    def clear(self):
        # self.outputTree.setData(None)
        self.hoverText.setPlainText('')