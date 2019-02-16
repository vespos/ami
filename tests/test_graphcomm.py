import pytest
import re
import zmq
import dill
import threading
import zmq.asyncio

from ami.data import DataTypes
from ami.comm import GraphCommHandler, AsyncGraphCommHandler


class FakeManager:
    def __init__(self, ctx, addr, conf, use_thread=True):
        self.ctx = ctx
        self.sock = self.ctx.socket(zmq.REP)
        self.sock.bind(addr)
        self.conf = conf
        self.graph = None
        self.feature_req = re.compile("fetch:(?P<name>.*)")
        if use_thread:
            self.thread = threading.Thread(target=self.run)
            self.thread.daemon = True
            self.thread.start()

    def feature_request(self, request):
        matched = self.feature_req.match(request)
        if matched:
            if matched.group('name') in self.conf['fetch']:
                self.sock.send_string('ok', zmq.SNDMORE)
                self.sock.send_pyobj(self.conf['fetch'][matched.group('name')])
            else:
                self.sock.send_string('error')
            return True
        else:
            return False

    def run(self):
        while True:
            request = self.sock.recv_string()
            if request == 'test_exit':
                self.sock.close()
                break
            elif self.feature_request(request):
                pass
            elif request in self.conf:
                if self.sock.getsockopt(zmq.RCVMORE):
                    payload = self.sock.recv()
                    if self.conf[request] == 'graph':
                        self.graph = dill.loads(payload)
                        self.sock.send_string('ok')
                    elif self.conf[request] == 'add_graph':
                        self.graph.add(dill.loads(payload))
                        self.sock.send_string('ok')
                    elif self.conf[request] == 'del_graph':
                        self.graph.remove(dill.loads(payload))
                        self.sock.send_string('ok')
                    else:
                        self.sock.send_string('error')
                elif self.conf[request] == 'graph':
                    self.sock.send(dill.dumps(self.graph))
                elif self.conf[request] is None:
                    self.sock.send_string('ok')
                else:
                    self.sock.send_pyobj(self.conf[request])
            else:
                while self.sock.getsockopt(zmq.RCVMORE):
                    self.sock.recv()
                self.sock.send_string('error')


@pytest.fixture(scope='function')
def graph_comm(request, ipc_dir, event_loop):
    ctxs = []
    addr = "ipc://%s/graphcomm_async" % ipc_dir
    # check the requested parameters
    if isinstance(request.param, dict):
        conf = request.param
        use_async = False
    else:
        use_async, conf = request.param

    if use_async:
        ctx = zmq.Context()
        manager = FakeManager(ctx, addr, conf)
        ctxs.append(ctx)
        comm = AsyncGraphCommHandler(addr)
        ctxs.append(comm._ctx)
    else:
        comm = GraphCommHandler(addr)
        ctx = comm._ctx
        manager = FakeManager(ctx, addr, conf)
        ctxs.append(ctx)

    yield comm, conf

    comm._sock.send_string('test_exit')
    manager.thread.join()
    # clean up all the zmq stuff
    for ctx in ctxs:
        ctx.destroy()


@pytest.mark.asyncio
@pytest.mark.parametrize('graph_comm',
                         [
                            (True, {'get_versions': (3, 0), 'get_graph_version': 3, 'get_features_version': 0}),
                            (True, {'get_versions': (0, 0), 'get_graph_version': 0, 'get_features_version': 0}),
                         ],
                         indirect=True)
async def test_versions_async(graph_comm):
    comm, conf = graph_comm

    # test the version properties of the comm handler
    assert await comm.versions == conf['get_versions']
    assert await comm.graphVersion == conf['get_graph_version']
    assert await comm.featuresVersion == conf['get_features_version']


@pytest.mark.parametrize('graph_comm',
                         [
                            {'get_versions': (3, 0), 'get_graph_version': 3, 'get_features_version': 0},
                            {'get_versions': (0, 0), 'get_graph_version': 0, 'get_features_version': 0},
                         ],
                         indirect=True)
def test_versions(graph_comm):
    comm, conf = graph_comm

    # test the version properties of the comm handler
    assert comm.versions == conf['get_versions']
    assert comm.graphVersion == conf['get_graph_version']
    assert comm.featuresVersion == conf['get_features_version']


@pytest.mark.asyncio
@pytest.mark.parametrize('graph_comm',
                         [
                            (
                                True,
                                {
                                    'get_names': {'cspad', 'delta_t', 'laser'},
                                    'get_features': {'cspad_img': DataTypes.Image}
                                }
                            ),
                            (True, {'get_names': set(), 'get_features': {}}),
                         ],
                         indirect=True)
async def test_names_async(graph_comm):
    comm, conf = graph_comm

    # test the names of features property give the expected value
    assert await comm.names == conf['get_names']
    assert await comm.features == conf['get_features']


@pytest.mark.parametrize('graph_comm',
                         [
                            {
                                'get_names': {'cspad', 'delta_t', 'laser'},
                                'get_features': {'cspad_img': DataTypes.Image}
                            },
                            {'get_names': set(), 'get_features': {}},
                         ],
                         indirect=True)
def test_names(graph_comm):
    comm, conf = graph_comm

    # test the names of features property give the expected value
    assert comm.names == conf['get_names']
    assert comm.features == conf['get_features']


@pytest.mark.asyncio
@pytest.mark.parametrize('graph_comm', [(True, {'clear_graph': None, 'reset_features': None})], indirect=True)
async def test_commands_async(graph_comm):
    comm, conf = graph_comm

    assert await comm.clear()
    assert await comm.reset()


@pytest.mark.parametrize('graph_comm', [{'clear_graph': None, 'reset_features': None}], indirect=True)
def test_commands(graph_comm):
    comm, conf = graph_comm

    assert comm.clear()
    assert comm.reset()


@pytest.mark.asyncio
@pytest.mark.parametrize('graph_comm',
                         [(True, {'set_graph': 'graph', 'get_graph': 'graph'})],
                         indirect=True)
async def test_graph_async(graph_comm, complex_graph):
    comm, conf = graph_comm

    # send and retrieve an empty graph
    assert await comm.update(None)
    assert await comm.graph is None

    # send a complex graph
    assert await comm.update(complex_graph)
    # check that the names of the return graph are the same
    assert (await comm.graph).names == complex_graph.names
    # check that the sources of the return graph are the same
    assert (await comm.graph).sources == complex_graph.sources


@pytest.mark.parametrize('graph_comm',
                         [{'set_graph': 'graph', 'get_graph': 'graph'}],
                         indirect=True)
def test_graph(graph_comm, complex_graph):
    comm, conf = graph_comm

    # send and retrieve an empty graph
    assert comm.update(None)
    assert comm.graph is None

    # send a complex graph
    assert comm.update(complex_graph)
    # check that the names of the return graph are the same
    assert comm.graph.names == complex_graph.names
    # check that the sources of the return graph are the same
    assert comm.graph.sources == complex_graph.sources


@pytest.mark.asyncio
@pytest.mark.parametrize('graph_comm',
                         [(
                            True,
                            {
                              'set_graph': 'graph',
                              'get_graph': 'graph',
                              'add_graph': 'add_graph',
                              'del_graph': 'del_graph'
                            }
                         )],
                         indirect=True)
async def test_modify_graph_async(graph_comm, complex_graph):
    comm, conf = graph_comm

    # send a complex graph
    assert await comm.update(complex_graph)

    # modify the graph by adding a map
    assert await comm.map('test_map', 'signal', 'test_value', lambda x: x + 2)
    # test that the map was added
    assert 'test_value' in (await comm.graph).names
    # remove the map
    assert await comm.remove('test_map')
    assert 'test_value' not in (await comm.graph).names

    # add a view to the graph
    assert await comm.view('cspad')
    # check that it is in the graph
    assert comm.auto('cspad') in (await comm.graph).names
    # remove the view
    assert await comm.remove('%s_view' % comm.auto('cspad'))
    assert comm.auto('cspad') not in (await comm.graph).names

    # add a pickN to the graph
    assert await comm.pickN('test_pickn', 'cspad', 'test_pick_val', N=2)
    # test that the pickn was added
    assert 'test_pick_val' in (await comm.graph).names
    # remove the pickn
    assert await comm.remove('test_pickn')
    assert 'test_pick_val' not in (await comm.graph).names


@pytest.mark.parametrize('graph_comm',
                         [{
                            'set_graph': 'graph',
                            'get_graph': 'graph',
                            'add_graph': 'add_graph',
                            'del_graph': 'del_graph'
                         }],
                         indirect=True)
def test_modify_graph(graph_comm, complex_graph):
    comm, conf = graph_comm

    # send a complex graph
    assert comm.update(complex_graph)

    # modify the graph by adding a map
    assert comm.map('test_map', 'signal', 'test_value', lambda x: x + 2)
    # test that the map was added
    assert 'test_value' in comm.graph.names
    # remove the map
    assert comm.remove('test_map')
    assert 'test_value' not in comm.graph.names

    # add a view to the graph
    assert comm.view('cspad')
    # check that it is in the graph
    assert comm.auto('cspad') in comm.graph.names
    # remove the view
    assert comm.remove('%s_view' % comm.auto('cspad'))
    assert comm.auto('cspad') not in comm.graph.names

    # add a pickN to the graph
    assert comm.pickN('test_pickn', 'cspad', 'test_pick_val', N=2)
    # test that the pickn was added
    assert 'test_pick_val' in comm.graph.names
    # remove the pickn
    assert comm.remove('test_pickn')
    assert 'test_pick_val' not in comm.graph.names


@pytest.mark.asyncio
@pytest.mark.parametrize('graph_comm',
                         [(True, {'fetch': {'cspad': 5}}), (True, {'fetch': {'cspad': None}})],
                         indirect=True)
async def test_fetch_async(graph_comm):
    comm, conf = graph_comm

    # test we can fetch something we expect
    assert await comm.fetch('cspad') == conf['fetch']['cspad']
    # check that None is return when we ask for something that isn't there
    assert await comm.fetch('delta_t') is None


@pytest.mark.parametrize('graph_comm',
                         [{'fetch': {'cspad': 5}}, {'fetch': {'cspad': None}}],
                         indirect=True)
def test_fetch(graph_comm):
    comm, conf = graph_comm

    # test we can fetch something we expect
    assert comm.fetch('cspad') == conf['fetch']['cspad']
    # check that None is return when we ask for something that isn't there
    assert comm.fetch('delta_t') is None