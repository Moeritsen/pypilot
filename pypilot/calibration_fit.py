#!/usr/bin/env python
#
#   Copyright (C) 2017 Sean D'Epagnier
#
# This Program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either
# version 3 of the License, or (at your option) any later version.  

import sys
import math
import time
import vector
from quaternion import *
import multiprocessing
from signalk.pipeserver import NonBlockingPipe

import numpy

debug=True
calibration_fit_period = 10  # run every 10 seconds

def FitLeastSq(beta0, f, zpoints):
    try:
        import scipy.optimize
    except:
        print "failed to load scientific library, cannot perform calibration update!"
        return False

    leastsq = scipy.optimize.leastsq(f, beta0, zpoints)
    return list(leastsq[0])

def FitPoints(points, sphere_fit):
    if debug:
        print 'FitPoints', len(points)

    if len(points) < 5:
        return False

    zpoints = [[], [], [], [], [], []]
    for i in range(6):
        zpoints[i] = map(lambda x : x[i], points)

    # with few sigma points, adjust only bias
    #Useful if no other cal can be found??
    def f_sphere_bias3(beta, x, r):
        return ((x[0]-beta[0])**2 + (x[1]-beta[1])**2 + (x[2]-beta[2])**2)/r**2 - 1
    sphere_bias_fit = FitLeastSq(sphere_fit[:3], f_sphere_bias3, (zpoints, sphere_fit[3])) + [sphere_fit[3]]
    sphere_bias_fit[3] = sphere_fit[3]
    if not sphere_bias_fit:
        print 'sphere bias failed!!! ', len(points)
        return False
    print 'sphere bias fit', sphere_bias_fit
#    sphere_fit = sphere_bias_fit + [sphere_fit[3]]

    def f_sphere3(beta, x):
        return ((x[0]-beta[0])**2 + (x[1]-beta[1])**2 + (x[2]-beta[2])**2) - beta[3]
    sphere_fit = FitLeastSq([0, 0, 0, 30], f_sphere3, zpoints)
    if not sphere_fit:
        print 'FitLeastSq sphere failed!!!! ', len(points)
        return False
        #sphere_fit[3] = abs(sphere_fit[3])
    sphere_fit[3] = math.sqrt(sphere_fit[3])
    if debug:
        print 'sphere fit', sphere_fit

    ellipsoid_fit = False
    '''
    if len(points) >= 10:
        def f_ellipsoid3(beta, x):
            return (x[0]-beta[0])**2 + (beta[4]*(x[1]-beta[1]))**2 + (beta[5]*(x[2]-beta[2]))**2 - beta[3]**2
        ellipsoid_fit = FitLeastSq(sphere_fit + [1, 1], f_ellipsoid3, zpoints)
        print 'ellipsoid_fit', ellipsoid_fit
    '''
    def f_new_bias3(beta, x):
        #print 'beta', beta
        b = numpy.matrix(map(lambda a, b : a - b, x[:3], beta[:3]))
        m = list(numpy.array(b.transpose()))
        r0 = map(lambda y : beta[3] - vector.dot(y, y), m)
        g = list(numpy.array(numpy.matrix(x[3:]).transpose()))
        fac = .8 # weight deviation less than magnitude
        # tan(x)/pi_2 restricts the dot product to the range -1 to 1
        r1 = map(lambda y, z : fac*beta[3]*(math.atan(beta[4])/math.pi*2 - vector.dot(y, z)/vector.norm(y)), m, g)
        return r0+r1
        
    new_bias_fit = FitLeastSq(sphere_fit[:4] + [0], f_new_bias3, zpoints)
    if not new_bias_fit:
        print 'FitLeastSq new bias fit failed!!!! ', len(points)
    new_bias_fit[3] = math.sqrt(new_bias_fit[3])
    new_bias_fit[4] = math.atan(new_bias_fit[4]) / math.pi * 2

    if debug:
        r12 = f_new_bias3(new_bias_fit, zpoints)
        l = len(r12)/2
        for i in range(l):
            m = zpoints[0][i], zpoints[1][i], zpoints[2][i]
            g = zpoints[3][i], zpoints[4][i], zpoints[5][i]
            #a0 = math.degrees(math.acos(vector.dot(m, g)/vector.norm(m)))
            #m = vector.sub(m, new_bias_fit[:3])
            #r1 = (new_bias_fit[4] - vector.dot(m, g)/vector.norm(m))
            #a1 = math.degrees(math.acos(vector.dot(m, g)/vector.norm(m)))
            #print i, r12[i]/new_bias_fit[3]**2, r12[l+i]/600, r1, a1
            
        print 'new bias fit', new_bias_fit, math.degrees(math.asin(new_bias_fit[4]))

    if not ellipsoid_fit:
        ellipsoid_fit = sphere_fit + [1, 1]
    return [new_bias_fit, sphere_bias_fit, sphere_fit, ellipsoid_fit]

def avg(fac, v0, v1):
    return map(lambda a, b : (1-fac)*a + fac*b, v0, v1)

class SigmaPoint(object):
    def __init__(self, compass, down):
        self.compass = compass
        self.down = down
        self.count = 1
        self.time = time.time()

    def add_measurement(self, compass, down):
        self.count += 1
        fac = max(1/self.count, .01)
        self.compass = avg(fac, self.compass, compass)
        self.down = avg(fac, self.down, down)
        self.time = time.time()

class SigmaPoints(object):
    sigma = 1.6**2 # distance between sigma points
    down_sigma = .05 # distance between down vectors
    max_sigma_points = 18

    def __init__(self):
        self.sigma_points = []
        self.lastpoint = False

    def AddPoint(self, compass, down):
        if not self.lastpoint:
            self.lastpoint = SigmaPoint(compass, down)
            return

        if vector.dist2(self.lastpoint.compass, compass) < SigmaPoints.sigma:
            fac = .005
            for i in range(3):
                self.lastpoint.compass[i] = fac*compass[i] + (1-fac)*self.lastpoint.compass[i]
                self.lastpoint.down[i] += down[i]
            self.lastpoint.count += 1
            return

        if self.lastpoint.count < 5: # require 5 measurements
            self.lastpoint = False
            return

        compass, down = self.lastpoint.compass, self.lastpoint.down
        for i in range(3):
            down[i] /= self.lastpoint.count
        self.lastpoint = False

        ind = 0
        for point in self.sigma_points:
            if vector.dist2(point.compass, compass) < SigmaPoints.sigma:
                point.add_measurement(compass, down)
                if ind > 0:
                    # put at front of list to speed up future tests
                    self.sigma_points = self.sigma_points[:ind-1] + [point] + \
                                        [self.sigma_points[ind-1]] + self.sigma_points[ind+1:]
                return
            ind += 1

        index = len(self.sigma_points)
        p = SigmaPoint(compass, down)
        if index == SigmaPoints.max_sigma_points:
            # replace point that is closest to other points
            mindisti = 0
            mindist = 1e20
            for i in range(len(self.sigma_points)):
                for j in range(i):
                    dist = vector.dist(self.sigma_points[i].compass, self.sigma_points[j].compass)
                    if dist < mindist:
                        # replace older point
                        if self.sigma_points[i].time < self.sigma_points[j].time:
                            mindisti = i
                        else:
                            mindisti = j
                        mindist = dist

            self.sigma_points[mindisti] = p
        else:
            self.sigma_points.append(p)

    def RemoveOldest(self):
        oldest_sigma = self.sigma_points[0]
        for sigma in self.sigma_points:
            if sigma.time < oldest_sigma.time:
                oldest_sigma = sigma
        self.sigma_points.remove(oldest_sigma)


def ComputeCoverage(sigma_points, bias):
    def ang(p):
        v = rotvecquat(vector.sub(p.compass, bias), vec2vec2quat(p.down, [0, 0, 1]))
        return math.atan2(v[1], v[0])

    angles = sorted(map(ang, sigma_points))
    #print 'angles', angles
                    
    max_diff = 0
    for i in range(len(angles)):
        diff = -angles[i]
        j = i+1
        if j == len(angles):
            diff += 2*math.pi
            j = 0
        diff += angles[j]
        max_diff = max(max_diff, diff)
    return max_diff    

def CalibrationProcess(points, fit_output, initial):
    import os
    if os.system('sudo chrt -pi 0 %d 2> /dev/null > /dev/null' % os.getpid()):
      print 'warning, failed to make calibration process idle, trying renice'
      if os.system("renice 20 %d" % os.getpid()):
          print 'warning, failed to renice calibration process'

    cal = SigmaPoints()

    while True:
        # each iteration remove oldest point if we have more than 12
        if len(cal.sigma_points) > 12:
            cal.RemoveOldest()
        
        t = time.time()
        addedpoint = False
        while time.time() - t < calibration_fit_period:
            p = points.recv(1)
            if p:
                cal.AddPoint(p[:3], p[3:6])
                addedpoint = True

        if not addedpoint: # don't bother to run fit if no new data
            continue
        # remove points older than 1 hour
        p = []
        print 'len', len(cal.sigma_points)
        for sigma in cal.sigma_points:
            # only use measurements in last hour
            if time.time() - sigma.time < 3600:
                p.append(sigma)
        cal.sigma_points = p

        # for now, require at least 6 points to agree well for update
        if len(p) < 6:
            continue

        # attempt to perform least squares fit
        p = []
        for sigma in cal.sigma_points:
            p.append(sigma.compass + sigma.down)
        fit = FitPoints(p, initial)
        if not fit:
            continue

        if debug:
            print 'fit', fit

        # if we have less than 10 points, only update bias
        if len(p) < 10:
            if debug:
                print 'bias update only'
            fit[0] = fit[1]
            
        # make sure the magnitude is sane
        mag = fit[0][3]
        if mag < 9 or mag > 70:
            print 'fit found field outside of normal earth field strength', mag
            fit[0] = fit[1] # use bias fit

        # sphere fit should basically agree with new bias
        if fit[0] != fit[1]:
            spherebias = fit[2][:3]
            sbd = vector.norm(vector.sub(bias, spherebias))
            if sbd > 4:
                if debug:
                    print 'sphere and newbias disagree', sbd
                fit[0] = fit[1]
            
        # test points for deviation, all must fall closely on a sphere
        bias = fit[0][:3]
        mag = fit[0][3]
        maxdev = 0
        for sigma in cal.sigma_points:
            dev = map(lambda a, b: (a-b)/mag, sigma.compass, bias)
            maxdev = max(abs(1-vector.norm(dev)), maxdev)
        if maxdev > .05:
            if debug:
                print 'maxdev > 0.05', maxdev
            # remove oldest point if too much deviation
            cal.RemoveOldest()
            continue # don't use this fit

        coverage = 360 - math.degrees(ComputeCoverage(cal.sigma_points, bias))
        if coverage < 120: # require 120 degrees
            if debug:
                print 'calibration: not enough coverage', coverage, 'degrees'
            continue

        # if the bias has sufficiently changed, otherwise the fit didn't change much
        n = map(lambda a, b: (a-b)**2, bias, initial[:3])
        d = n[0]+n[1]+n[2]
        if d < .1:
            if debug:
                print 'insufficient change in bias, calibration ok'
            continue

        print 'coverage', coverage, 'new fit:', fit, 'sphere bias difference', sbd
        initial = fit[0]
        fit_output.send((fit, map(lambda p : p.compass + p.down, cal.sigma_points)), False)
                                 
class MagnetometerAutomaticCalibration(object):
    def __init__(self, cal_pipe, initial):
        self.cal_pipe = cal_pipe
        self.sphere_fit = initial
        points, self.points = NonBlockingPipe('points pipe', True)
        self.fit_output, fit_output = NonBlockingPipe('fit output', True)

        self.process = multiprocessing.Process(target=CalibrationProcess, args=(points, fit_output, self.sphere_fit))
        #print 'start cal process'
        self.process.start()

    def __del__(self):
        print 'terminate calibration process'
        self.process.terminate()

    def AddPoint(self, point):
        self.points.send(point, False)
    
    def UpdatedCalibration(self):
        result = self.fit_output.recv()
        if not result:
            return

        # use new bias fit
        self.cal_pipe.send(tuple(result[0][0][:3]))
        return result

def ExtraFit():
#        return [sphere_fit, 1, ellipsoid_fit]
    def f_rotellipsoid3(beta, x):
        return x[0]**2 + (beta[1]*x[1] + beta[3]*x[0])**2 + (beta[2]*x[2] + beta[4]*x[0] + beta[5]*x[1])**2 - beta[0]**2
        a = x[0]-beta[0]
        b = x[1]-beta[1]
        c = x[2]-beta[2]
        return (a)**2 + (beta[4]*b + beta[6]*a)**2 + (beta[5]*c + beta[7]*a + beta[8]*b)**2 - beta[3]**2
    def f_ellipsoid3_cr(beta, x, cr):
        a = x[0]-beta[0]
        b = x[1]-beta[1]
        c = x[2]-beta[2]
        return (a)**2 + (beta[4]*b + cr[0]*a)**2 + (beta[5]*c + cr[1]*a + cr[2]*b)**2 - beta[3]**2

        # if the ellipsoid fit is sane
    if abs(ellipsoid_fit[4]-1) < .2 and abs(ellipsoid_fit[5]-1) < .2:
        cpoints = map(lambda a, b : a - b, zpoints[:3], ellipsoid_fit[:3])
        rotellipsoid_fit = FitLeastSq(ellipsoid_fit[3:] + [0, 0, 0], f_rotellipsoid3, cpoints)
        #print 'rotellipsoid_fit', rotellipsoid_fit
        ellipsoid_fit2 = FitLeastSq(ellipsoid_fit[:3] + rotellipsoid_fit[:3], f_ellipsoid3_cr, (zpoints, rotellipsoid_fit[3:]))
        #print 'ellipsoid_fit2', ellipsoid_fit2

        cpoints = map(lambda a, b : a - b, zpoints[:3], ellipsoid_fit2[:3])
        rotellipsoid_fit2 = FitLeastSq(ellipsoid_fit[3:] + [0, 0, 0], f_rotellipsoid3, cpoints)
        print 'rotellipsoid_fit2', rotellipsoid_fit2
    else:
        ellipsoid_fit = False

    def f_uppermatrixfit(beta, x):
            b = numpy.matrix(map(lambda a, b : a - b, x[:3], beta[:3]))
            r = numpy.matrix([beta[3:6], [0]+list(beta[6:8]), [0, 0]+[beta[8]]])
            print 'b', beta

            m = r * b
            m = list(numpy.array(m.transpose()))
            r0 = map(lambda y : 1 - vector.dot(y, y), m)

            return r0

    def f_matrixfit(beta, x, efit):
            b = numpy.matrix(map(lambda a, b : a - b, x[:3], efit[:3]))
            r = numpy.matrix([[1,       beta[0], beta[1]],
                              [beta[2], efit[4], beta[3]],
                              [beta[4], beta[5], efit[5]]])

            m = r * b
            m = list(numpy.array(m.transpose()))
            r0 = map(lambda y : efit[3]**2 - vector.dot(y, y), m)
            #return r0

            g = list(numpy.array(numpy.matrix(x[3:]).transpose()))
            r1 = map(lambda y, z : beta[6] - vector.dot(y, z), m, g)

            return r0+r1

    def f_matrix2fit(beta, x, efit):
            b = numpy.matrix(map(lambda a, b : a - b, x[:3], beta[:3]))
            r = numpy.matrix([[1,       efit[0], efit[1]],
                              [efit[2], beta[4], efit[3]],
                              [efit[4], efit[5], beta[5]]])

            m = r * b
            m = list(numpy.array(m.transpose()))
            r0 = map(lambda y : beta[3]**2 - vector.dot(y, y), m)
            #return r0

            g = list(numpy.array(numpy.matrix(x[3:]).transpose()))
            r1 = map(lambda y, z : beta[6] - vector.dot(y, z), m, g)

            return r0+r1

    if False:
         matrix_fit = FitLeastSq([0, 0, 0, 0, 0, 0, 0], f_matrixfit, (zpoints, ellipsoid_fit))
         #print 'matrix_fit', matrix_fit

         matrix2_fit = FitLeastSq(ellipsoid_fit + [matrix_fit[6]], f_matrix2fit, (zpoints, matrix_fit))
         #print 'matrix2_fit', matrix2_fit

         matrix_fit2 = FitLeastSq(matrix_fit, f_matrixfit, (zpoints, matrix2_fit))
         print 'matrix_fit2', matrix_fit2

         matrix2_fit2 = FitLeastSq(matrix2_fit[:6] + [matrix_fit2[6]], f_matrix2fit, (zpoints, matrix_fit2))
         print 'matrix2_fit2', matrix2_fit2

    def rot(v, beta):
        sin, cos = math.sin, math.cos
        v = vector.normalize(v)
        #            q = angvec2quat(beta[0], [0, 1, 0])
        #            return rotvecquat(v, q)
        v1 = [v[0]*cos(beta[0]) + v[2]*sin(beta[0]),
              v[1],
              v[2]*cos(beta[0]) - v[0]*sin(beta[0])]

        v2 = [v1[0],
              v1[1]*cos(beta[1]) + v1[2]*sin(beta[1]),
              v1[2]*cos(beta[1]) - v1[1]*sin(beta[1])]

        v3 = [v2[0]*cos(beta[2]) + v2[1]*sin(beta[2]),
              v2[1]*cos(beta[2]) - v2[0]*sin(beta[1]),
              v2[2]]
            
        return v3

    def f_quat(beta, x, sphere_fit):
        sphere_fit = numpy.array(sphere_fit)
        n = [x[0]-sphere_fit[0], x[1]-sphere_fit[1], x[2]-sphere_fit[2]]
        q = [1 - vector.norm(beta[:3])] + list(beta[:3])
        q = angvec2quat(vector.norm(beta[:3]), beta[:3])
        m = map(lambda v : rotvecquat(vector.normalize(v), q), zip(n[0], n[1], n[2]))
#        m = map(lambda v : rot(v, beta), zip(n[0], n[1], n[2]))

        m = numpy.array(zip(*m))
        d = m[0]*x[3] + m[1]*x[4] + m[2]*x[5]
        return beta[3] - d

    quat_fit = FitLeastSq([0, 0, 0, 0], f_quat, (zpoints, sphere_fit))
    #    q = [1 - vector.norm(quat_fit[:3])] + list(quat_fit[:3])
    q = angvec2quat(vector.norm(quat_fit[:3]), quat_fit[:3])

    print 'quat fit', q, math.degrees(angle(q)), math.degrees(math.asin(quat_fit[3]))
    
    def f_rot(beta, x, sphere_fit):
        sphere_fit = numpy.array(sphere_fit)
        n = [x[0]-sphere_fit[0], x[1]-sphere_fit[1], x[2]-sphere_fit[2]]
        m = map(lambda v : rot(v, beta), zip(n[0], n[1], n[2]))
        m = numpy.array(zip(*m))

        d = m[0]*x[3] + m[1]*x[4] + m[2]*x[5]
        return beta[3] - d

    rot_fit = FitLeastSq([0, 0, 0, 0], f_rot, (zpoints, sphere_fit))
    print 'rot fit', rot_fit, math.degrees(rot_fit[0]), math.degrees(rot_fit[1]), math.degrees(rot_fit[2]), math.degrees(math.asin(min(1, max(-1, rot_fit[3]))))
    
    

if __name__ == '__main__':
    r = 38.0
    s = math.sin(math.pi/4) * r
    debug = True
    points = [[ r, 0, 0, 0, 0, 1],
              [ s*1.1, s, 0, 0, 0, 1],
              [ 0, r, 0, 0, 0, 1],
              [-s*1.1, s, 0, 0, 0, 1],
              [-r, 0, 0, 0, 0, 1],
              [-s,-s, 0, 0, 0, 1],
              [ 0,-r, 0, 0, 0, 1],
              [ s,-s, 0, 0, 0, 1],

              [ r, 0, 0, 0, 1, 0],
              [ s*1.1, 0, s, 0, 1, 0],
              [ 0, 0, r, 0, 1, 0],
              [-s, 0, s, 0, 1, 0],
              [-r, 0, 0, 0, 1, 0],
              [-s, 0,-s, 0, 1, 0],
              [ 0, 0,-r, 0, 1, 0],
              [ s, 0,-s, 0, 1, 0]]

    
    #FitPoints(points, [0, 0, 0, r])

    points = [[9.076,19.17,32.66,-0.078,-0.037,0.996],[8.106,14.431,32.2,-0.077,-0.042,0.996],[9.184,16.653,32.451,-0.07,-0.032,0.997],[11.645,21.557,32.988,-0.077,-0.042,0.996],[20.508,27.569,32.798,-0.075,-0.044,0.996],[22.091,28.787,32.86,-0.076,-0.046,0.996],[11.541,19.82,32.848,-0.075,-0.046,0.996],[10.679,18.367,32.569,-0.076,-0.043,0.996],[8.628,11.927,31.855,-0.075,-0.045,0.996],[14.149,22.908,33.247,-0.072,-0.04,0.997],[18.136,25.664,32.971,-0.074,-0.038,0.997],[16.213,24.721,33.405,-0.071,-0.048,0.996]]
    FitPoints(points, [0, 0, 0, 30])
    
    #allpoints = [points1, points2, points3, points4, points5]
    #for points in allpoints:
    #    FitPoints(points, [0, 0, 0, r])
