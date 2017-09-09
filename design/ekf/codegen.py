import sympy as sp
from sympy.printing.python import PythonPrinter

C_UFs = {
    "Heaviside": "Heaviside",
    "DiracDelta": "DiracDelta",
    "Abs": "fabsf",
    "Min": "fminf",
    "Max": "fmaxf"
}

Py_UFs = {
    "Abs": "abs",
    "Min": "min",
    "Max": "max",
    "atan": "arctan"
}

pyprint = PythonPrinter(None)


def ccode(expr):
    return sp.ccode(expr, user_functions=C_UFs)


def pycode(expr):
    return pyprint._str(expr.subs(Py_UFs))


def ccode_matrix(matexpr, indent):
    lines = []
    H, W = matexpr.shape
    indent = ' '*indent
    for j in range(H):
        lines.append(', '.join([ccode(expr) for expr in matexpr[W*j:W*(j+1)]]))
    return (',\n' + indent).join(lines) + ';'


def pycode_matrix(matexpr, indent):
    lines = []
    H, W = matexpr.shape
    indent = ' '*indent
    for j in range(H):
        lines.append('[' + ', '.join(
            [pycode(expr) for expr in matexpr[W*j:W*(j+1)]]) + ']')
    return (',\n' + indent).join(lines)


class EKFGen:
    def __init__(self, X):
        self.X = X
        self.N = len(X)
        self.fh = None
        self.fcc = None
        self.fpy = None

    def open(self, outdir_cc, outdir_py, x0, P0):
        # FIXME: make class name, file name, path flexible
        self.fh = open("%s/ekf.h" % outdir_cc, "w")
        self.fcc = open("%s/ekf.cc" % outdir_cc, "w")
        self.fpy = open("%s/ekf.py" % outdir_py, "w")
        N = self.N

        print >>self.fh, '''#ifndef MODEL_EKF_H_
#define MODEL_EKF_H_
#include <Eigen/Dense>

// This file is auto-generated by ekf/codegen.py. DO NOT EDIT.


class EKF {
 public:
  EKF();
  
  void Reset();
'''
        print >>self.fcc, '''#include <Eigen/Dense>
#include "ekf.h"

// This file is auto-generated by ekf/codegen.py. DO NOT EDIT.

using Eigen::VectorXf;
using Eigen::MatrixXf;

#define Min(x, y) fminf(x, y)
#define Max(x, y) fmaxf(x, y)

static inline float Heaviside(float x) {
  return x < 0 ? 0 : 1;
}

static inline float DiracDelta(float x) {
  return x == 0;
}

EKF::EKF() : x_(%d), P_(%d, %d) {
  Reset();
}

''' % (N, N, N)
        print >>self.fcc, 'void EKF::Reset() {'
        print >>self.fcc, '  x_ <<', ccode_matrix(x0, 8)
        print >>self.fcc, '  P_.setIdentity();'
        print >>self.fcc, '  P_.diagonal() <<', ccode_matrix(P0, 4)
        print >>self.fcc, '}\n'

        print >>self.fpy, '''#!/usr/bin/env python
import numpy as np
from numpy import sin, cos, tan, exp, sqrt, sign, arctan as atan, abs as Abs
from __builtin__ import min as Min, max as Max

# This file is auto-generated by ekf/codegen.py. DO NOT EDIT.


def Heaviside(x):
    return 1 * (x > 0)


def DiracDelta(x, v=1):
    return x == 0 and v or 0


def initial_state():'''
        print >>self.fpy, '    x = np.float32('
        print >>self.fpy, '        ' + pycode_matrix(x0.T, 8)
        print >>self.fpy, '    )'
        print >>self.fpy, '    P = np.diag('
        print >>self.fpy, '        ' + pycode_matrix(P0.T, 8)
        print >>self.fpy, '    )'
        print >>self.fpy, '\n    return x, P\n\n'

    def close(self):
        print >>self.fh, '''

  Eigen::VectorXf& GetState() { return x_; }
  Eigen::MatrixXf& GetCovariance() { return P_; }

 private:
  Eigen::VectorXf x_;
  Eigen::MatrixXf P_;
};

#endif  // MODEL_EKF_H_
'''
        self.fh.close()
        self.fcc.close()
        self.fpy.close()

    def generate_predict(self, f, u, Q, dt):
        ''' Generate a method for doing an EKF prediction step. f(X, u) -> X is
        the system dynamics model, X is a symbolic vector of each state
        variable name u is the symbolic control input vector. '''
        N = self.N
        F = f.jacobian(self.X)
        U = f.jacobian(u)
        vs, es = sp.cse([F - sp.eye(N), f - self.X, Q], optimizations='basic',
                        symbols=sp.numbered_symbols("tmp"))

        self.generate_predict_cc(f, u, Q, dt, N, F, vs, es)
        self.generate_predict_py(f, u, Q, dt, N, F, vs, es)

        vs, es = sp.cse([f - self.X, F - sp.eye(N), U], optimizations='basic',
                        symbols=sp.numbered_symbols("tmp"))
        self.generate_controlstep_py(f, u, dt, N, vs, es)

    def generate_predict_py(self, f, u, Q, dt, N, F, vs, es):
        # maybe we should just lambdify this or ufuncify
        arglist = ['x', 'P', pycode(dt)] + [pycode(ui) for ui in u]
        print >>self.fpy, "def predict(%s):" % ', '.join(arglist)

        print >>self.fpy, "    (%s) = x" % ', '.join([pycode(x) for x in self.X])
        print >>self.fpy, ""

        for x in vs:
            print >>self.fpy, '    %s = %s' % (pycode(x[0]), pycode(x[1]))

        print >>self.fpy, '\n    F = np.eye(%d)' % N
        for i, term in enumerate(es[0]):
            if term != 0:
                print >>self.fpy, '    F[%d, %d] += %s' % (
                    i / N, i % N, pycode(term))

        print >>self.fpy, '    Q = np.float32([', ', '.join(
            [ccode(x*x) for x in es[2]]) + '])'

        for i, term in enumerate(es[1]):
            if term != 0:
                print >>self.fpy, '    x[%d] += %s' % (i, pycode(term))

        print >>self.fpy, '\n    P = np.dot(F, np.dot(P, F.T)) + Delta_t * np.diag(Q)'

        print >>self.fpy, "    return x, P\n\n"

    def generate_controlstep_py(self, f, u, dt, N, vs, es):
        ''' Generate a prediction step suitable for model-predictive control, which
        returns Jacobians w.r.t. the control inputs '''
        print >>self.fpy, "def step(x, u, %s):" % pycode(dt)

        print >>self.fpy, "    (%s) = x" % ', '.join([pycode(x) for x in self.X])
        print >>self.fpy, "    (%s) = u" % ', '.join([pycode(x) for x in u])
        print >>self.fpy, ""

        for x in vs:
            print >>self.fpy, '    %s = %s' % (pycode(x[0]), pycode(x[1]))

        print >>self.fpy, '\n    F = np.eye(%d)' % N
        for i, term in enumerate(es[1]):
            if term != 0:
                print >>self.fpy, '    F[%d, %d] += %s' % (
                    i / N, i % N, pycode(term))

        print >>self.fpy, '\n    J = np.zeros((%d, %d))' % (N, len(u))
        for i, term in enumerate(es[2]):
            if term != 0:
                print >>self.fpy, '    J[%d, %d] = %s' % (
                    i / len(u), i % len(u), pycode(term))

        for i, term in enumerate(es[0]):
            if term != 0:
                print >>self.fpy, '    x[%d] += %s' % (i, pycode(term))

        print >>self.fpy, "    return x, F, J\n\n"

    def generate_predict_cc(self, f, u, Q, dt, N, F, vs, es):
        N = self.N

        arglist = ["float " + ccode(dt)] + ["float " + ccode(ui) for ui in u]
        print >>self.fcc, "void EKF::Predict(%s) {" % ', '.join(arglist)
        print >>self.fh, '  void Predict(%s);' % ', '.join(arglist)

        for i, elem in enumerate(self.X):
            if elem in (f - self.X).free_symbols:
                print >>self.fcc, "  float %s = x_[%d];" % (ccode(elem), i)

        print >>self.fcc, ""

        for x in vs:
            print >>self.fcc, '  float %s = %s;' % (ccode(x[0]), ccode(x[1]))

        print >>self.fcc, '\n  MatrixXf F(%d, %d);' % (N, N)
        print >>self.fcc, '  F.setIdentity();'
        for i, term in enumerate(es[0]):
            if term != 0:
                print >>self.fcc, '  F(%d, %d) += %s;' % (i / N, i % N, ccode(term))

        print >>self.fcc, '\n  VectorXf Q(%d);' % N
        print >>self.fcc, '  Q <<', ', '.join([ccode(x*x) for x in es[2]]) + ';'

        for i, term in enumerate(es[1]):
            if term != 0:
                print >>self.fcc, '  x_[%d] += %s;' % (i, ccode(term))
        
        print >>self.fcc, '\n  P_ = F * P_ * F.transpose();'
        print >>self.fcc, '  P_.diagonal() += ' + ccode(dt) + ' * Q;'
        print >>self.fcc, '}\n'


    def generate_measurement(self, name, h_x, h_z, z_k, R_k):
        H = h_x.jacobian(self.X)
        M = h_z.jacobian(z_k)
        y_k = h_z - h_x
        vs, es = sp.cse([y_k, H, M], optimizations='basic',
                        symbols=sp.numbered_symbols("tmp"))

        self.generate_measurement_cc(
            name, h_x, h_z, z_k, R_k, H, M, y_k, vs, es)

        self.generate_measurement_py(
            name, h_x, h_z, z_k, R_k, H, M, y_k, vs, es)

    def generate_measurement_cc(self, name, h_x, h_z, z_k, R_k,
                                H, M, y_k, vs, es):
        ''' Generate a method for doing an EKF measurement update. h(X) -> X is the
        measurement function of the state, X is the symbolic state vector, z_k is
        the symbolic measurement vector, R is the measurement noise covariance
        in terms of z_k (either full matrix or vector; if a vector, treated as std.
        deviations along the diagonal)

        h_x is the measurement vector in terms of state variables
        h_z is the same vector but in terms of the measurement (possibly identical)
        '''

        N = self.N
        arglist = ["float " + ccode(ui) for ui in z_k]
        if not R_k.is_Matrix and R_k.is_Symbol:
            arglist.append("Eigen::MatrixXf Rk")
        name = name[0].upper() + name[1:]
        print >>self.fcc, 'bool EKF::Update%s(%s) {' % (name, ', '.join(arglist))
        print >>self.fh, '  bool Update%s(%s);' % (name, ', '.join(arglist))

        for i, elem in enumerate(self.X):
            if elem in h_x.free_symbols:
                print >>self.fcc, "  float %s = x_[%d];" % (ccode(elem), i)

        for x in vs:
            print >>self.fcc, '  float %s = %s;' % (ccode(x[0]), ccode(x[1]))

        print >>self.fcc, ""

        print >>self.fcc, '\n  VectorXf yk(%d);' % (y_k.shape[0])
        print >>self.fcc, '  yk <<', ccode_matrix(es[0], 8)

        print >>self.fcc, '\n  MatrixXf Hk(%d, %d);' % H.shape
        print >>self.fcc, '  Hk <<', ccode_matrix(es[1], 8)

        if R_k.is_Matrix:
            if R_k.shape[1] > 1:
                print >>self.fcc, '\n  MatrixXf Rk(%d, %d);' % R_k.shape
                print >>self.fcc, '  Rk <<', ccode_matrix(R_k, 8)
            else:
                print >>self.fcc, '\n  VectorXf Rk(%d);' % R_k.shape[0]
                # in this case we need to square it also
                # print >>self.fcc, '  Rk <<', ccode_matrix(R_k, 8)
                print >>self.fcc, '  Rk <<', ', '.join([ccode(x*x) for x in R_k]) + ';'

        if not M.is_Identity:
            print >>self.fcc, '  MatrixXf Mk(%d, %d);' % M.shape
            print >>self.fcc, '  Mk <<', ccode_matrix(es[2], 8)
            print >>self.fcc, '  Rk = Mk * Rk * Mk.transpose();'

        Sshape = es[1].shape[0]
        if Sshape >= 2 and Sshape <= 6:
            Sshape = str(Sshape)
        else:
            Sshape = 'X'
        if R_k.is_Matrix and R_k.shape[1] == 1:
            print >>self.fcc, \
                '\n  Eigen::Matrix%sf S = Hk * P_ * Hk.transpose();' % (
                    Sshape)
            print >>self.fcc, '  S.diagonal() += Rk;'
        else:
            print >>self.fcc, \
                '\n  Eigen::Matrix%sf S = Hk * P_ * Hk.transpose() + Rk;' % (
                    Sshape)
        print >>self.fcc, '  MatrixXf K = P_ * Hk.transpose() * S.inverse();'
        # FIXME: return false if S is not invertible?

        print >>self.fcc, '\n  x_.noalias() += K * yk;'
        print >>self.fcc, \
            '  P_ = (MatrixXf::Identity(%d, %d) - K*Hk) * P_;' % (N, N)

        print >>self.fcc, '  return true;'
        print >>self.fcc, '}\n'

    def generate_measurement_py(self, name, h_x, h_z, z_k, R_k,
                                H, M, y_k, vs, es):
        arglist = ['x', 'P'] + [str(u_i) for u_i in z_k]
        if not R_k.is_Matrix and R_k.is_Symbol:
            arglist = arglist + ["Rk"]
        print >>self.fpy, 'def update_%s(%s):' % (name, ', '.join(arglist))

        for i, elem in enumerate(self.X):
            if elem in h_x.free_symbols:
                print >>self.fpy, "    %s = x[%d]" % (pycode(elem), i)

        for x in vs:
            print >>self.fpy, '    %s = %s' % (pycode(x[0]), pycode(x[1]))

        print >>self.fpy, '\n    yk = np.float32('
        print >>self.fpy, ' '*8 + pycode_matrix(es[0].T, 8) + ')'

        print >>self.fpy, '\n    Hk = np.float32(['
        print >>self.fpy, ' '*8 + pycode_matrix(es[1], 8) + '])'

        if R_k.is_Matrix:
            if R_k.shape[1] > 1:
                print >>self.fpy, '\n    Rk = np.float32(['
                print >>self.fpy, ' '*8 + pycode_matrix(R_k, 8) + '])'
            else:
                # in this case we need to square it also
                print >>self.fpy, '\n    Rk = np.diag(['
                print >>self.fpy, ' '*8 + ', '.join(
                    [pycode(x*x) for x in R_k]) + '])'

        if not M.is_Identity:
            print >>self.fpy, '    Mk = np.float32(['
            print >>self.fpy, ' '*8 + pycode_matrix(es[2], 8) + '])'
            print >>self.fpy, '    Rk = np.dot(Mk, np.dot(Rk, Mk.T))'

        print >>self.fpy, '\n    S = np.dot(Hk, np.dot(P, Hk.T)) + Rk'
        print >>self.fpy, '\n    LL = -np.dot(yk, np.dot(np.linalg.inv(S), yk)) - 0.5 * np.log(2 * np.pi * np.linalg.det(S))'
        # linalg.solve? lstsq?
        #print >>self.fpy, '    K = np.dot(P, np.dot(Hk.T, np.linalg.inv(S)))'
        print >>self.fpy, '    K = np.linalg.lstsq(S, np.dot(Hk, P))[0].T'
        # FIXME: return false if S is not invertible?

        print >>self.fpy, '''    x += np.dot(K, yk)
    KHk = np.dot(K, Hk)
    P = np.dot((np.eye(len(x)) - KHk), P)
    return x, P, LL

'''
