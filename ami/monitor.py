#!/usr/bin/env python
import sys
import argparse
import asyncio
import logging
import pickle
import zmq
import tornado.ioloop
import numpy as np
import panel as pn
import holoviews as hv
from networkfox import modifiers
from ami import LogConfig, Defaults
from ami.client import GraphMgrAddress
from ami.comm import Ports, ZMQ_TOPIC_DELIM
from ami.data import Deserializer


logger = logging.getLogger(__name__)
pn.extension()
hv.extension('bokeh')

options = {'axiswise': True, 'framewise': True, 'shared_axes': False, 'show_grid': True, 'tools': ['hover']}
hv.opts.defaults(hv.opts.Curve(**options),
                 hv.opts.Scatter(**options),
                 hv.opts.Image(**options),
                 hv.opts.Histogram(**options))


class AsyncFetcher(object):

    def __init__(self, topics, terms, addr):
        self.addr = addr
        self.ctx = zmq.asyncio.Context()
        self.poller = zmq.asyncio.Poller()
        self.sockets = {}
        self.data = {}
        self.timestamps = {}
        self.deserializer = Deserializer()
        self.update_topics(topics, terms)

    @property
    def reply(self):
        heartbeats = set(self.timestamps.values())
        res = {}

        if self.data.keys() == self.subs and len(heartbeats) == 1:
            heartbeats.pop()

            for name, topic in self.topics.items():
                res[name] = self.data[topic]

        elif self.optional.issuperset(self.data.keys()):
            for name, topic in self.topics.items():
                if topic in self.data:
                    res[name] = self.data[topic]

        return res

    def update_topics(self, topics, terms):
        self.topics = topics
        self.terms = terms
        self.names = list(topics.keys())
        self.subs = set(topics.values())
        self.optional = set([value for key, value in topics.items() if type(key) is modifiers.optional])

        for name, sock_count in self.sockets.items():
            sock, count = sock_count
            self.poller.unregister(sock)
            sock.close()

        self.sockets = {}
        self.view_subs = {}

        for term, name in terms.items():
            if name not in self.sockets:
                topic = topics[name]
                sub_topic = "view:%s:%s" % (self.addr.name, topic)
                self.view_subs[sub_topic] = topic
                sock = self.ctx.socket(zmq.SUB)
                sock.setsockopt_string(zmq.SUBSCRIBE, sub_topic + ZMQ_TOPIC_DELIM)
                sock.connect(self.addr.view)
                self.poller.register(sock, zmq.POLLIN)
                self.sockets[name] = (sock, 1)  # reference count
            else:
                sock, count = self.sockets[name]
                self.sockets[name] = (sock, count+1)

    async def fetch(self):
        for sock, flag in await self.poller.poll():
            if flag != zmq.POLLIN:
                continue
            topic = await sock.recv_string()
            topic = topic.rstrip('\0')
            heartbeat = await sock.recv_pyobj()
            reply = await sock.recv_serialized(self.deserializer, copy=False)
            self.data[self.view_subs[topic]] = reply
            self.timestamps[self.view_subs[topic]] = heartbeat

    def close(self):
        for name, sock_count in self.sockets.items():
            sock, count = sock_count
            self.poller.unregister(sock)
            sock.close()

        self.ctx.destroy()


class PlotWidget():

    def __init__(self, topics=None, terms=None, addr=None, **kwargs):
        self.fetcher = AsyncFetcher(topics, terms, addr)
        self.terms = terms

        self.idx = kwargs.get('idx', (0, 0))
        self.pipes = {}
        self._plot = None

        if kwargs.get('pipes', True):
            for term, name in terms.items():
                self.pipes[name] = hv.streams.Pipe(data=[])

    async def update(self):
        while True:
            await self.fetcher.fetch()
            if self.fetcher.reply:
                self.data_updated(self.fetcher.reply)

    def close(self):
        self.fetcher.close()

    @property
    def plot(self):
        return self._plot


class ScalarWidget(PlotWidget):

    def __init__(self, topics=None, terms=None, addr=None, **kwargs):
        super().__init__(topics, terms, addr, **kwargs)
        label = kwargs.get('name', '')
        self._plot = pn.Row(pn.widgets.StaticText(value=f"<b>{label}:</b>"), pn.widgets.StaticText())

    def data_updated(self, data):
        for term, name in self.terms.items():
            self._plot[-1].value = str(data[name])


class ObjectWidget(ScalarWidget):

    def data_updated(self, data):
        for k, v in data.items():
            txt = f"variable: {k}<br/>type: {type(v)}<br/>value: {v}"
            if type(v) is np.ndarray:
                txt += f"<br/>shape: {v.shape}<br/>dtype: {v.dtype}"
            self._plot[-1].value = txt


class ImageWidget(PlotWidget):

    def __init__(self, topics=None, terms=None, addr=None, **kwargs):
        super().__init__(topics, terms, addr, **kwargs)
        label = kwargs.get('name', '')
        self._plot = hv.DynamicMap(self.trace(label),
                                   streams=list(self.pipes.values())).hist().opts(toolbar='right')

    def data_updated(self, data):
        for term, name in self.terms.items():
            self.pipes[name].send(data[name])

    def trace(self, label):
        def func(data):
            x1, y1 = getattr(data, 'shape', (0, 0))
            img = hv.Image(data, label=label, bounds=(0, 0, x1, y1)).opts(colorbar=True)
            return img

        return func


class HistogramWidget(PlotWidget):

    def __init__(self, topics=None, terms=None, addr=None, **kwargs):
        super().__init__(topics, terms, addr, pipes=False, **kwargs)
        self.num_terms = int(len(terms)/2) if terms else 0

        label = kwargs.get('name', '')
        plots = []

        for i in range(0, self.num_terms):
            y = self.terms[f"Counts.{i}" if i > 0 else "Counts"]
            self.pipes[y] = hv.streams.Pipe(data=[])
            plots.append(hv.DynamicMap(lambda data: hv.Histogram(data, label=label),
                                       streams=[self.pipes[y]]))

        self._plot = hv.Overlay(plots).collate() if len(plots) > 1 else plots[0]

    def data_updated(self, data):
        for i in range(0, self.num_terms):
            x = self.terms[f"Bins.{i}" if i > 0 else "Bins"]
            y = self.terms[f"Counts.{i}" if i > 0 else "Counts"]
            name = y

            x = data[x]
            y = data[y]

            self.pipes[name].send((x, y))


class Histogram2DWidget(PlotWidget):

    def __init__(self, topics=None, terms=None, addr=None, **kwargs):
        super().__init__(topics, terms, addr, pipes=False, **kwargs)
        label = kwargs.get('name', '')
        self.pipes['Counts'] = hv.streams.Pipe(data=[])
        self._plot = hv.DynamicMap(lambda data: hv.Image(data, label=label).opts(colorbar=True),
                                   streams=list(self.pipes.values())).hist().opts(toolbar='right')

    def data_updated(self, data):
        xbins = data[self.terms['XBins']]
        ybins = data[self.terms['YBins']]
        counts = data[self.terms['Counts']]
        self.pipes['Counts'].send((xbins, ybins, counts.transpose()))


class ScatterWidget(PlotWidget):

    def __init__(self, topics=None, terms=None, addr=None, **kwargs):
        super().__init__(topics, terms, addr, pipes=False, **kwargs)
        self.num_terms = int(len(terms)/2) if terms else 0

        title = kwargs.get('name', '')
        plots = []

        for i in range(0, self.num_terms):
            x = self.terms[f"X.{i}" if i > 0 else "X"]
            y = self.terms[f"Y.{i}" if i > 0 else "Y"]
            name = " vs ".join((y, x))
            self.pipes[name] = hv.streams.Pipe(data=[])
            plots.append(hv.DynamicMap(lambda data: hv.Scatter(data, label=name).opts(title=title),
                                       streams=[self.pipes[name]]))

        self._plot = hv.Overlay(plots).collate() if len(plots) > 1 else plots[0]

    def data_updated(self, data):
        for i in range(0, self.num_terms):
            x = self.terms[f"X.{i}" if i > 0 else "X"]
            y = self.terms[f"Y.{i}" if i > 0 else "Y"]
            name = " vs ".join((y, x))

            x = data[x]
            y = data[y]

            self.pipes[name].send((x, y))


class WaveformWidget(PlotWidget):

    def __init__(self, topics=None, terms=None, addr=None, **kwargs):
        super().__init__(topics, terms, addr, **kwargs)

        title = kwargs.get('name', '')
        plots = []

        for term, name in terms.items():
            plots.append(hv.DynamicMap(lambda data: hv.Curve(data, label=name).opts(title=title),
                                       streams=[self.pipes[name]]))

        self._plot = hv.Overlay(plots).collate() if len(plots) > 1 else plots[0]

    def data_updated(self, data):
        for term, name in self.terms.items():
            self.pipes[name].send((np.arange(0, len(data[name])), data[name]))


class LineWidget(PlotWidget):

    def __init__(self, topics=None, terms=None, addr=None, **kwargs):
        super().__init__(topics, terms, addr, pipes=False, **kwargs)
        self.num_terms = int(len(terms)/2) if terms else 0

        title = kwargs.get('name', '')
        plots = []

        for i in range(0, self.num_terms):
            x = self.terms[f"X.{i}" if i > 0 else "X"]
            y = self.terms[f"Y.{i}" if i > 0 else "Y"]
            name = " vs ".join((y, x))
            self.pipes[name] = hv.streams.Pipe(data=[])
            plots.append(hv.DynamicMap(lambda data: hv.Curve(data, label=name).opts(title=title),
                                       streams=[self.pipes[name]]))

        self._plot = hv.Overlay(plots).collate() if len(plots) > 1 else plots[0]

    def data_updated(self, data):
        for i in range(0, self.num_terms):
            x = self.terms[f"X.{i}" if i > 0 else "X"]
            y = self.terms[f"Y.{i}" if i > 0 else "Y"]
            name = " vs ".join((y, x))

            if x not in data or y not in data:
                continue

            x = data[x]
            y = data[y]
            # sort the data using the x-axis, otherwise the drawn line is messed up
            x, y = zip(*sorted(zip(x, y)))
            self.pipes[name].send((x, y))


class TimeWidget(LineWidget):

    def __init__(self, topics=None, terms=None, addr=None, **kwargs):
        super().__init__(topics, terms, addr, **kwargs)


class Monitor():

    def __init__(self, graphmgr_addr):
        self.graphmgr_addr = graphmgr_addr
        self.ctx = zmq.asyncio.Context()

        self.export = self.ctx.socket(zmq.SUB)
        self.export.setsockopt_string(zmq.SUBSCRIBE, "")
        self.export.connect(self.graphmgr_addr.comm)

        self.lock = asyncio.Lock()
        self.plot_metadata = {}
        self.plots = {}
        self.tasks = {}

        self.layout_row = pn.Row()

        self.enabled_plots = pn.widgets.CheckBoxGroup(name='Plots', options=[])
        self.enabled_plots.param.watch(self.plot_checked, 'value')
        self.layout_row.append(self.enabled_plots)

        self.layout = pn.GridSpec(sizing_mode='stretch_both')
        self.layout_row.append(self.layout)

        self.row = 0
        self.col = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self):
        for name, plot in self.plots.items():
            plot.close()

        self.export.close()
        self.ctx.destroy()

    async def run(self, loop):
        asyncio.create_task(self.process_msg())
        asyncio.create_task(self.start_server(loop))
        asyncio.create_task(self.monitor_tasks())

    async def start_server(self, loop):
        self.server = pn.serve(self.layout_row, loop=loop)

    async def monitor_tasks(self):
        while True:
            async with self.lock:
                cancelled_tasks = set()
                for name, task in self.tasks.items():
                    try:
                        raise task.exception()
                    except asyncio.CancelledError:
                        cancelled_tasks.add(name)
                    except pickle.UnpicklingError:
                        continue
                    except asyncio.InvalidStateError:
                        continue

                for name in cancelled_tasks:
                    self.tasks.pop(name, None)

            await asyncio.sleep(1)

    async def plot_checked(self, event):
        async with self.lock:
            names = event.new

            for name in names:
                metadata = self.plot_metadata[name]

                if name in self.plots:
                    widget = self.plots[name]
                    self.tasks[name] = asyncio.create_task(widget.update())
                    continue

                if metadata['type'] not in globals():
                    print("UNSUPPORTED PLOT TYPE:", metadata['type'])
                    continue

                widget = globals()[metadata['type']]
                widget = widget(topics=metadata['topics'], terms=metadata['terms'],
                                addr=self.graphmgr_addr, name=name, idx=(self.row, self.col))
                self.plots[name] = widget
                self.layout[self.row, self.col] = widget.plot
                self.col = (self.col + 1) % 3
                self.row = self.row + 1 if self.col == 0 else self.row
                self.tasks[name] = asyncio.create_task(widget.update())

            names = event.old

            for name in names:
                if name in self.tasks:
                    self.tasks[name].cancel()

    async def process_msg(self):
        while True:
            topic = await self.export.recv_string()
            graph = await self.export.recv_string()
            exports = await self.export.recv_pyobj()

            if self.graphmgr_addr.name != graph:
                continue

            if topic == 'store':
                async with self.lock:
                    plots = exports['plots']
                    new_plots = set(plots.keys()).difference(self.plot_metadata.keys())
                    for name in new_plots:
                        self.plot_metadata[name] = plots[name]

                    removed_plots = set(self.plot_metadata.keys()).difference(plots.keys())
                    for name in removed_plots:
                        self.plot_metadata.pop(name, None)
                        task = self.tasks.get(name, None)
                        if task and not task.cancelled():
                            task.cancel()
                        widget = self.plots.pop(name, None)
                        if widget:
                            row, col = widget.idx
                            del self.layout[row, col]
                            widget.close()

                    self.enabled_plots.options = list(self.plot_metadata.keys())


def run_monitor(graph_name, export_addr, view_addr):
    logger.info('Starting monitor')

    graphmgr_addr = GraphMgrAddress(graph_name, export_addr, view_addr, None)

    loop = tornado.ioloop.IOLoop.current()
    with Monitor(graphmgr_addr) as mon:
        asyncio.ensure_future(mon.run(loop))
        loop.start()


def main():
    parser = argparse.ArgumentParser(description='AMII GUI Client')

    parser.add_argument(
        '-H',
        '--host',
        default=Defaults.Host,
        help='hostname of the AMII Manager (default: %s)' % Defaults.Host
    )

    parser.add_argument(
        '-p',
        '--port',
        type=int,
        nargs=2,
        default=(Ports.Export, Ports.View),
        help='port for manager (GUI) export and view (default: %d, %d)' %
             (Ports.Export, Ports.View)
    )

    parser.add_argument(
        '-g',
        '--graph-name',
        default=Defaults.GraphName,
        help='the name of the graph used (default: %s)' % Defaults.GraphName
    )

    parser.add_argument(
        '--log-level',
        default=LogConfig.Level,
        help='the logging level of the application (default %s)' % LogConfig.Level
    )

    parser.add_argument(
        '--log-file',
        help='an optional file to write the log output to'
    )

    args = parser.parse_args()
    graph = args.graph_name
    export, view = args.port
    export_addr = "tcp://%s:%d" % (args.host, export)
    view_addr = "tcp://%s:%d" % (args.host, view)

    log_handlers = [logging.StreamHandler()]
    if args.log_file is not None:
        log_handlers.append(logging.FileHandler(args.log_file))
    log_level = getattr(logging, args.log_level.upper(), logging.INFO)
    logging.basicConfig(format=LogConfig.Format, level=log_level, handlers=log_handlers)

    try:
        return run_monitor(graph, export_addr, view_addr)
    except KeyboardInterrupt:
        logger.info("Monitor killed by user...")
        return 0


if __name__ == '__main__':
    sys.exit(main())