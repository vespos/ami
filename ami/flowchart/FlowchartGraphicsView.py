from pyqtgraph.Qt import QtGui, QtCore
from pyqtgraph.widgets.GraphicsView import GraphicsView
from pyqtgraph.graphicsItems.ViewBox import ViewBox


class FlowchartGraphicsView(GraphicsView):

    sigHoverOver = QtCore.Signal(object)
    sigClicked = QtCore.Signal(object)

    def __init__(self, widget, *args):
        GraphicsView.__init__(self, *args, useOpenGL=False)
        self.setAcceptDrops(True)
        self._vb = FlowchartViewBox(widget, lockAspect=True, invertY=True)
        self.setCentralItem(self._vb)
        self.setRenderHint(QtGui.QPainter.Antialiasing, True)

    def viewBox(self):
        return self._vb

    def dragEnterEvent(self, ev):
        ev.accept()


class FlowchartViewBox(ViewBox):

    def __init__(self, widget, *args, **kwargs):
        ViewBox.__init__(self, *args, **kwargs)
        self.widget = widget
        self.setAcceptDrops(True)

    def getMenu(self, ev):
        # called by ViewBox to create a new context menu
        self._fc_menu = QtGui.QMenu()
        self._subMenus = self.getContextMenus(ev)
        for menu in self._subMenus:
            self._fc_menu.addMenu(menu)
        return self._fc_menu

    def getContextMenus(self, ev):
        # called by scene to add menus on to someone else's context menu
        menu = self.widget.buildMenu(ev.scenePos())
        menu.setTitle("Add node")
        return [menu, ViewBox.getMenu(self, ev)]

    def decode_data(self, arr):
        data = []
        item = {}

        ds = QtCore.QDataStream(arr)
        while not ds.atEnd():
            ds.readInt32()
            ds.readInt32()

            map_items = ds.readInt32()
            for i in range(map_items):

                key = ds.readInt32()

                value = QtCore.QVariant()
                ds >> value
                item[QtCore.Qt.ItemDataRole(key)] = value

                data.append(item)

        return data

    def dropEvent(self, ev):
        if ev.mimeData().hasFormat('application/x-qabstractitemmodeldatalist'):
            arr = ev.mimeData().data('application/x-qabstractitemmodeldatalist')
            nodeType = self.decode_data(arr)[0][0].value()
            try:
                self.widget.chart.createNode(nodeType, pos=self.mapToView(ev.pos()))
            except Exception:
                pass
            ev.accept()
        else:
            ev.ignore()