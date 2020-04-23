from typing import Any
from ami.flowchart.Node import Node
from ami.flowchart.library.common import CtrlNode
from ami.flowchart.library.CalculatorWidget import LogicalCalculatorWidget
import ami.graph_nodes as gn


class Filter(Node):

    def __init__(self, name):
        super(Filter, self).__init__(name,
                                     terminals={'Condition': {'io': 'condition', 'ttype': Any},
                                                'Out': {'io': 'out', 'ttype': bool}},
                                     filter=True,
                                     allowAddCondition=False)

    def output_vars(self):
        return [self.name()]


class FilterOff(Filter):

    """
    FilterOff
    """

    nodeName = "FilterOff"

    def __init__(self, name):
        super(FilterOff, self).__init__(name)

    def to_operation(self, inputs, conditions=[]):
        outputs = self.output_vars()
        node = gn.FilterOff(name=self.name()+'_operation', condition_needs=list(conditions.values()), outputs=outputs,
                            parent=self.name())
        return node


class FilterOn(Filter):

    """
    FilterOn
    """

    nodeName = "FilterOn"

    def __init__(self, name):
        super(FilterOn, self).__init__(name)

    def to_operation(self, inputs, conditions=[]):
        outputs = self.output_vars()
        node = gn.FilterOn(name=self.name()+'_operation', condition_needs=list(conditions.values()), outputs=outputs,
                           parent=self.name())
        return node


class If(CtrlNode):
    """
    If
    """

    nodeName = "If"

    def __init__(self, name):
        super().__init__(name, terminals={'In': {'io': 'in', 'ttype': Any},
                                          'Out': {'io': 'out', 'ttype': bool}},
                         allowAddInput=True,
                         allowAddCondition=False,
                         filter=True)

        self.operation = ""

    def output_vars(self):
        return [self.name()]

    def display(self, topics, terms, addr, win, **kwargs):
        if self.widget is None:
            self.widget = LogicalCalculatorWidget(terms, win, self.operation)
            self.widget.sigStateChanged.connect(self.state_changed)

        return self.widget

    def saveState(self):
        state = super().saveState()
        state['ctrl'] = {'operation': self.operation}

        if self.widget:
            state['widget'] = self.widget.saveState()
        else:
            state['widget'] = {'operation': self.operation}

        return state

    def restoreState(self, state):
        super().restoreState(state)

        self.operation = state['ctrl']['operation']

        if self.widget:
            self.widget.restoreState(state['widget'])

    def to_operation(self, inputs, conditions={}):
        outputs = self.output_vars()
        args = []
        expr = self.operation

        # sympy doesn't like symbols name likes Sum.0.Out, need to remove dots.
        for arg in self.input_vars().values():
            rarg = arg.replace('.', '')
            args.append(rarg)
            expr = expr.replace(arg, rarg)

        args = ', '.join(args)
        func = eval(f"lambda {args}: {expr}")

        node = gn.FilterOn(name=self.name()+'_operation', condition_needs=list(inputs.values()),
                           outputs=outputs, parent=self.name(), condition=func)

        return node