# -*- coding: latin-1 -*-
# Copyright (c) 2008 Pycircuit Development Team
# See LICENSE for details.

import numpy as np
from pycircuit.utilities.param import Parameter, ParameterDict
from numpy import array, delete, linalg, size, zeros, concatenate, pi, \
    zeros, alltrue, maximum, conj, dot, imag, eye
from scipy import optimize
from pycircuit.circuit import Circuit, SubCircuit, VS,IS,R,C,L,Diode, gnd, \
    defaultepar, instjoin
import symbolic
from pycircuit.post.waveform import Waveform
from pycircuit.post.result import IVResultDict
from pycircuit.post.internalresult import InternalResultDict
from copy import copy
import numeric
import types

np.set_printoptions(precision=4)

class NoConvergenceError(Exception):
    pass

class SingularMatrix(Exception):
    pass

class CircuitResult(IVResultDict, InternalResultDict):
    """Result class for analyses that returns voltages and currents"""
    def __init__(self, circuit, x, xdot = None):
        super(CircuitResult, self).__init__()

        nodes = circuit.nodes

        if x != None:
            for xvalue, node in zip(x[:len(nodes)], nodes):
                self[circuit.get_node_name(node)] =  xvalue
            for i, data in enumerate(zip(x[len(nodes):], circuit.branches)):
                xvalue, branch = data
                self['i' + str(i)] = xvalue

        self.circuit = circuit
        self.x = x
        self.xdot = xdot

    def v(self, plus, minus=None):
        result = self.circuit.extract_v(self.x, plus, minus)

        if isinstance(result, Waveform):
            if minus != None:
                result.ylabel = 'v(%s,%s)'%(str(plus), str(minus))
            else:
                result.ylabel = 'v(%s)'%(str(plus))
            result.yunit = 'V'

        return result

    def i(self, term):
        """Return terminal current i(term)"""
        return self.circuit.extract_i(self.x, term, xdot = self.xdot)    

class CircuitResultAC(CircuitResult):
    """Result class for analyses that returns voltages and currents"""
    def __init__(self, circuit, xdcop, x, xdot = None):
        super(CircuitResultAC, self).__init__(circuit, x, xdot)
        self.xdcop = xdcop

    def i(self, term):
        """Return terminal current i(term)"""
        return self.circuit.extract_i(self.x, term, xdot = self.xdot, 
                                      linearized=True, 
                                      xdcop = self.xdcop)    

def remove_row_col(matrices, n):
    result = []
    for A in matrices:
        for axis in range(len(A.shape)):
            A=delete(A, [n], axis=axis)
        result.append(A)
    return tuple(result)

class Analysis(object):
    parameters = []
    def __init__(self, cir, epar = defaultepar, 
                 toolkit=None, **kvargs):

        self.options = ParameterDict(*self.parameters, **kvargs)

        if toolkit == None:
            toolkit = numeric

        self.toolkit = toolkit

        if hasattr(toolkit, 'setup_analysis'):
            epar = epar.copy()
            toolkit.setup_analysis(epar)

        self.cir = cir
        self.result = None
        self.epar = epar

class Tran_spec(Analysis):
    """Simple transient analyis class.

    Starting a transient analysis class with support for linear
    dynamic elements.

    Using an explicit method (forward euler) because of it's 
    simplicity. Without any other tricks, backward euler (being
    an implicit method) is otherwise more suited for stiff systems 
    of ODEs.
    
    The usual companion models are used.

    i(t) = c*dv/dt
    v(t) = L*di/dt

    forward euler:
    i(n+1) = c/dt*(v(n) - v(n-1)) = geq*v(n) + Ieq
    v(n+1) = L/dt*(i(n) - i(n-1)) = req*i(n) + Veq

    For each time-step:
    (G+Geq)*x(n) + u + ueq

    Linear circuit example:
    >>> c = SubCircuit()
    >>> n1 = c.add_node('net1')
    >>> n2 = c.add_node('net2')
    >>> c['Is'] = IS(gnd, n1, i=10)    
    >>> c['R1'] = R(n1, gnd, r=1)
    >>> c['R2'] = R(n1, n2, r=1e3)
    >>> c['R3'] = R(n2, gnd, r=100e3)
    >>> c['C'] = C(n2, gnd, c=1e-5)
    >>> tran = Tran_spec(c)
    >>> res = tran.solve(tend=20e-3)
    >>> tran.result[-1][1] #node 2 of last x
    6.29

    Linear circuit example:
    >>> c = SubCircuit()
    >>> n1 = c.add_node('net1')
    >>> c['Is'] = IS(gnd, n1, i=0.1)
    >>> c['R'] = R(n1, gnd, r=1e2)
    >>> c['C'] = C(n1, gnd, c=1e-6)
    >>> c['L'] = L(n1, gnd, L=1e-3)
    >>> tran = Tran_spec(c)
    >>> nodeset = array([-1.0,0.,0.])
    >>> res = tran.solve(tend=100e-6,timestep=1e-6)
    >>> tran.result[-1][0]
    0.951

    """
    # Very incomplete TODO-list:
    # solve with implicit method (with fsolve)
    # generalise to using different coefficients for other methods
    # try using telescopic projective solver with explicit method
    # http://ews.uiuc.edu/~mrgates2/ode/projective-ode.pdf
    # much more work with presenting and storing results
    # try differential evolution for determining steady-state solution

    def solve(self, refnode=gnd, tend=1e-3, x0=None, timestep=1e-5):

        X = [] # will contain a list of all x-vectors
        irefnode=self.cir.get_node_index(refnode)
        n = self.cir.n
        dt = timestep

        if x0 is None:
            x = zeros(n)
        else:
            x = x0

        order=2 #number of past x-values needed
        for i in xrange(order):
            X.append(copy(x))

        #create vector with timepoints and a more fitting dt
        times,dt=np.linspace(0,tend,num=int(tend/dt),endpoint=True,retstep=True)

        for t in times:
            G=self.cir.G(X[-1])
            Geq=self.cir.C(X[-1])/dt
            u=self.cir.u(X[-1])
            ueq=-dot(Geq,X[-2])
            G+=Geq
            u+=ueq
            # Refer the voltages to the reference node by removing
            # the rows and columns that corresponds to this node
            G,u=remove_row_col((G,u), irefnode)

            x=linalg.solve(G,-u)
            # Insert reference node voltage
            x = concatenate((x[:irefnode], array([0.0]), x[irefnode:]))
#            print(x,t)
            X.append(copy(x))
        self.result = X
        return x #returns the final value

class Tran_imp(Analysis):
    """Simple transient analyis class.

    i(t) = c*dv/dt
    v(t) = L*di/dt

    backward euler:
    The usual companion models are used.
    i(n+1) = c/dt*(v(n+1) - v(n)) = geq*v(n+1) + Ieq
    v(n+1) = L/dt*(i(n+1) - i(n)) = req*i(n+1) + Veq

    def J(x): return G(x)+Geq(x)
    def F(x): return u(x)+ueq(x0)
    x0=x(n)
    x(n+1) = fsolve(F, x0, fprime=J)

    Linear circuit example:
    >>> c = SubCircuit()
    >>> n1 = c.add_node('net1')
    >>> n2 = c.add_node('net2')
    >>> c['Is'] = IS(gnd, n1, i=10)    
    >>> c['R1'] = R(n1, gnd, r=1)
    >>> c['R2'] = R(n1, n2, r=1e3)
    >>> c['R3'] = R(n2, gnd, r=100e3)
    >>> c['C'] = C(n2, gnd, c=1e-5)
    >>> tran = Tran_imp(c)
    >>> res = tran.solve(tend=20e-3)
    >>> tran.result[-1][1] #node 2 of last x
    6.29

    Linear circuit example:
    >>> c = SubCircuit()
    >>> n1 = c.add_node('net1')
    >>> c['Is'] = IS(gnd, n1, i=0.1)
    >>> c['R'] = R(n1, gnd, r=1e2)
    >>> c['C'] = C(n1, gnd, c=1e-6)
    >>> c['L'] = L(n1, gnd, L=1e-3)
    >>> tran = Tran_imp(c)
    >>> nodeset = array([-1.0,0.])
    >>> res = tran.solve(tend=100e-6,timestep=1e-6)
    >>> tran.result[-1][0]
    0.951

    """
    def solve_timestep(self, x0, dt, refnode=gnd):
        n=self.cir.n
        x0 = x0
        dt = dt

        ## Refer the voltages to the reference node by removing
        ## the rows and columns that corresponds to this node
        irefnode = self.cir.get_node_index(refnode)

        def func(x):
            x = concatenate((x[:irefnode], array([0.0]), x[irefnode:]))
            xlast = concatenate((x0[:irefnode], array([0.0]), x0[irefnode:]))
            Geq = self.cir.C(x)/dt
            ueq = -dot(Geq,xlast)
            f =  self.cir.i(x) + dot(Geq, x) + self.cir.u(x) + ueq
#            f =  self.cir.u(x) + ueq
            (f,) = remove_row_col((f,), irefnode)
            return array(f, dtype=float)
        def fprime(x):
            x = concatenate((x[:irefnode], array([0.0]), x[irefnode:]))
            Geq = self.cir.C(x)/dt
            J = self.cir.G(x) + Geq
            (J,) = remove_row_col((J,), irefnode)
            return array(J, dtype=float)

        rtol = 1e-4

        x = fsolve(func, x0, fprime=fprime)
        # Insert reference node voltage
        #x = concatenate((x[:irefnode], array([0.0]), x[irefnode:]))
        return x


    def solve(self, refnode=gnd, tend=1e-3, x0=None, timestep=1e-6):

        X = [] # will contain a list of all x-vectors
        irefnode=self.cir.get_node_index(refnode)
        n = self.cir.n
        dt = timestep
        if x0 is None:
            x = zeros(n-1) #currently without reference node !
        else:
            x = x0 # reference node not included !
        order=1 #number of past x-values needed
        for i in xrange(order):
            X.append(copy(x))

        #create vector with timepoints and a more fitting dt
        times,dt=np.linspace(0,tend,num=int(tend/dt),endpoint=True,retstep=True)

        for t in times:
            x=self.solve_timestep(X[-1],dt)
#            print(x,t)
            X.append(copy(x))
        self.result = X
        return (x,t) #returns the final value

class AC(Analysis):
    """
    AC analysis class

    Examples:
    
    >>> from sympy import Symbol, simplify
    >>> c = SubCircuit()
    >>> n1 = c.add_node('net1')
    >>> c['vs'] = VS(n1, gnd, vac=Symbol('V'))
    >>> c['R'] = R(n1, gnd, r=Symbol('R'))
    >>> res = AC(c, toolkit=symbolic).solve(Symbol('s'), complexfreq=True)
    >>> res['net1']
    V
    >>> res['i0']
    -V/R

    >>> c = SubCircuit()
    >>> n1 = c.add_node('net1')
    >>> c['vs'] = VS(n1, gnd, vac=1.5)
    >>> c['R'] = R(n1, gnd, r=1e3)
    >>> c['C'] = C(n1, gnd, c=1e-12)
    >>> ac = AC(c)
    >>> res = ac.solve(freqs=array([1e6, 2e6]))
    >>> res.keys()
    ['i0', 'gnd', 'net1']
    >>> ac.result['net1']
    Waveform(array([ 1000000.,  2000000.]), array([ 1.5+0.j,  1.5+0.j]))
    >>> res.v(n1, gnd)
    Waveform(array([ 1000000.,  2000000.]), array([ 1.5+0.j,  1.5+0.j]))
    >>> res.i('vs.minus')
    Waveform(array([ 1000000.,  2000000.]), array([ 0.0015 +9.4248e-06j,  0.0015 +1.8850e-05j]))

"""

    def solve(self, freqs, refnode=gnd, complexfreq = False, u = None):
        toolkit = self.toolkit

        cir = self.cir
        n = cir.n
        
        x = zeros(n) ## FIXME, this should be calculated from the dc analysis
        
        G = cir.G(x)
        C = cir.C(x)

        ## Allow for custom stimuli, mainly used by other analyses
        if u == None:
            u = cir.u(x, analysis='ac')

        ## Refer the voltages to the reference node by removing
        ## the rows and columns that corresponds to this node
        irefnode = cir.get_node_index(refnode)
        G,C,u = remove_row_col((G,C,u), irefnode)

        if complexfreq:
            ss = freqs
        else:
            ss = 2j*pi*freqs

        def solvecircuit(s):
            x = toolkit.linearsolver(s*C + G, -u)

            # Insert reference node voltage
            return concatenate((x[:irefnode], array([0.0]), x[irefnode:]))

        if isiterable(freqs):
            out = [solvecircuit(s) for s in ss]
            # Swap frequency and x-vector dimensions
            xac = [Waveform(freqs, value, 
                            xlabels=('frequency',), xunits=('Hz',))
                   for value in array(out).swapaxes(0,1)]
            xacdot = [ss * xi for xi in xac]
        else:
            xac = solvecircuit(ss)
            xacdot = ss * xac

        self.result = CircuitResultAC(cir, x, xac, xacdot)

        return self.result

class TransimpedanceAnalysis(Analysis):
    """Calculates transimpedance or current-gain vector

    This function calculates the transimpedances from a current injected
    in every node to a voltage or branch current in the circuit. If 
    current=*True* the current gain is calculated.

    The outbranches is a list of Branch objects and in the voltage mode
    the output voltage is taken between the positive and negative node of
    the branch. The result is a list of transimpedance or current-gain
    vectors.

    Note, the element corresponding the reference node is eliminated in the
    result

    """
    def solve(self, freqs, outbranches, currentoutput=False,
              complexfreq=False, refnode=gnd):
        toolkit = self.toolkit 

        n = self.cir.n
        x = zeros(n) # This should be the x-vector at the DC operating point

        ## Complex frequency variable
        if complexfreq:
            s = freqs
        else:
            s = 2j*pi*freqs

        epar = self.epar
        G = self.cir.G(x, epar)
        C = self.cir.C(x, epar)

        ## Refer the voltages to the gnd node by removing
        ## the rows and columns that corresponds to this node
        irefnode = self.cir.nodes.index(refnode)
        G,C = remove_row_col((G,C), irefnode)

        # Calculate the reciprocal G and C matrices
        Yreciprocal = G.T + s*C.T

        Yreciprocal = toolkit.toMatrix(Yreciprocal)

        result = []
        for branch in outbranches:
            ## Stimuli
            if currentoutput:
                u = zeros(n, dtype=int)
                ibranch = self.cir.get_branch_index(branch)
                u[ibranch] = -1
            else:
                u = zeros(n, dtype=int)
                ## The signed is swapped because the u-vector appears in the lhs
                u[self.cir.get_node_index(branch.plus)] = -1
                u[self.cir.get_node_index(branch.minus)] = 1

            u, = remove_row_col((u,), irefnode)

            ## Calculate transimpedances from currents in each nodes to output
            result.append(toolkit.linearsolver(Yreciprocal, -u))

        return result


class Noise(Analysis):
    """Noise analysis that calculates input and output referred noise.
    
    The analysis is using the adjoint admittance matrix method to calculate the 
    transfers from each noise source to the output.
    
    Example, calculate input referred noise of a voltage divider:

    >>> c = SubCircuit()
    >>> n1 = c.add_node('net1')
    >>> n2 = c.add_node('net2')
    >>> c['vs'] = VS(n1, gnd, vac=1.0)
    >>> c['R1'] = R(n1, n2, r=9e3)
    >>> c['R2'] = R(n2, gnd, r=1e3)
    >>> res = Noise(c, inputsrc='vs', outputnodes=(n2, gnd)).solve(0)
    >>> print res['Svnout']
    (1.4904e-17+0j)
    >>> print res['Svninp']
    (1.4904e-15+0j)
    >>> print res['gain']
    (0.1+0j)
    
    Symbolic example:
    
    >>> from sympy import Symbol, simplify
    >>> c = SubCircuit()
    >>> kT = Symbol('kT')
    >>> R1=Symbol('R1', real=True)
    >>> R2=Symbol('R2', real=True)
    >>> n1,n2 = c.add_nodes('net1', 'net2')
    >>> c['vs'] = VS(n1, gnd, v=Symbol('V'))
    >>> c['R1'] = R(n1, n2, r=R1)
    >>> c['R2'] = R(n2, gnd, r=R2)
    >>> noise = Noise(c, inputsrc='vs', outputnodes=(n2, gnd), toolkit=symbolic)
    >>> res = noise.solve(Symbol('s'), complexfreq=True)
    >>> simplify(res['Svnout'])
    4*R1*R2*kT/(R1 + R2)
    >>> simplify(res['Svninp'])
    (4*R1*R2*kT + 4*kT*R1**2)/R2
    >>> simplify(res['gain'] - R2 / (R1 + R2))
    0

    """
    def __init__(self, circuit, inputsrc=None, 
                 outputnodes=None, outputsrc=None,
                 toolkit=None):
        """
        Initiate a noise analysis.

        Parameters
        ----------
        circuit : Circuit instance
            The circuit to be analyzed
        inputsrc : VS or IS instance
            A voltage or current source in the circuit where the input noise
            should be referred to
        outputnodes : tuple
            A tuple with the output nodes (outputpos outputneg)
        outputsrc: VS instance
            The voltage source where the output current noise is measured
        """

        Analysis.__init__(self, circuit, toolkit=toolkit)
    
        if not (outputnodes != None or outputsrc != None):
            raise ValueError('Output is not specified')
        elif outputnodes != None and outputsrc != None:
            raise ValueError('Cannot measure both output current and voltage '
                             'noise')
        
        if not (type(inputsrc) is types.StringType and \
                outputsrc == None or type(outputsrc) is types.StringType):
            raise ValueError('Sources must be given as instance names')

        self.inputsrc_name = inputsrc
        self.inputsrc = self.cir[inputsrc]
        self.outputnodes = outputnodes
        self.outputsrc_name = outputsrc
        if self.outputsrc_name:
            self.outputsrc = self.cir[outputsrc]

    def solve(self, freqs, refnode=gnd, complexfreq=False):
        toolkit = self.toolkit

        n = self.cir.n
        x = zeros(n) # This should be the x-vector at the DC operating point

        ## Complex frequency variable
        if complexfreq:
            s = freqs
        else:
            s = 2j*pi*freqs

        epar = self.epar
        G = self.cir.G(x, epar)
        C = self.cir.C(x, epar)
        CY = self.cir.CY(x, imag(s),epar)

        # Calculate output voltage noise
        if self.outputnodes != None:
            u = zeros(n, dtype=int)
            ioutp, ioutn = (self.cir.get_node_index(node) 
                            for node in self.outputnodes)
            u[ioutp] = -1
            u[ioutn] = 1
        # Calculate output current noise
        else:
            u = zeros(n, dtype=int)
            plus_term = instjoin(self.outputsrc_name, 'plus')
            branch = self.cir.get_terminal_branch(plus_term)[0]
            ibranch = self.cir.get_branch_index(branch)
            u[ibranch] = -1

        ## Refer the voltages to the gnd node by removing
        ## the rows and columns that corresponds to this node
        irefnode = self.cir.nodes.index(refnode)
        G,C,u,CY = remove_row_col((G,C,u,CY), irefnode)

        # Calculate the reciprocal G and C matrices
        Yreciprocal = G.T + s*C.T

        Yreciprocal, u = (toolkit.toMatrix(A) for A in (Yreciprocal, u))

        ## Calculate transimpedances from currents in each nodes to output
        zm = toolkit.linearsolver(Yreciprocal, -u)

        xn2out = dot(dot(zm.reshape(1,size(zm)), CY), conj(zm))

        xn2out = xn2out[0]

        # Store results
        result = InternalResultDict()

        if self.outputnodes != None:
            result['Svnout'] = xn2out
        elif self.outputsrc != None:
            result['Sinout'] = xn2out

        # Calculate the gain from the input voltage source by using the 
        # transimpedance vector to find the transfer from the branch voltage of
        # the input source to the output
        gain = None
        if isinstance(self.inputsrc, VS):
            gain = self.cir.extract_i(zm, 
                                      instjoin(self.inputsrc_name, 'plus'),
                                      refnode=refnode, 
                                      refnode_removed=True)
            result['gain'] = gain
            result['Svninp'] = xn2out / abs(gain)**2

        elif isinstance(self.inputsrc, IS):
            plus_node = instjoin(self.inputsrc_name, 'plus')
            gain = self.cir.extract_v(zm, 
                                      self.cir.get_node(plus_node), 
                                      refnode=refnode, refnode_removed=True)
            result['gain'] = gain
            result['Sininp'] = xn2out / abs(gain)**2

        return result


def isiterable(object):
    return hasattr(object,'__iter__')
