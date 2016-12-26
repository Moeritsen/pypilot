#!/usr/bin/env python
#
#   Copyright (C) 2016 Sean D'Epagnier
#
# This Program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either
# version 3 of the License, or (at your option) any later version.  

import time, sys
from signalk.client import SignalKClient
import json, math, numpy
import pypilot.quaternion

from OpenGL.GLUT import *
from OpenGL.GLU import *
from OpenGL.GL import *

def TranslateAfter(x, y, z):
    m = glGetFloatv(GL_MODELVIEW_MATRIX)
    glLoadIdentity()
    glTranslatef(x, y, z)
    glMultMatrixf(m)

def RotateAfter(ang, x, y, z):
    m = glGetFloatv(GL_MODELVIEW_MATRIX)
    glLoadIdentity()
    glRotatef(ang, x, y, z)
    glMultMatrixf(m)

def rotate_mouse(dx, dy):
    RotateAfter((dx**2 + dy**2)**.1, dy, dx, 0)

def GLArray(points):
    vpoints = (GLfloat * (3*len(points)))()
    i = 0
    for point in points:
        for j in range(3):
            vpoints[i+j] = point[j]
        i += 3
    return vpoints

class Shape(object):
    def __init__(self, vertexes):
        self.array = GLArray(vertexes)

    def draw(self):
        glEnableClientState(GL_VERTEX_ARRAY);
        glVertexPointer(3, GL_FLOAT, 0, self.array)
        glDrawArrays(GL_QUADS, 0, len(self.array)/3)
        glDisableClientState(GL_VERTEX_ARRAY);

class Spherical(Shape):
    def __init__(self, beta, f, lons, lats):
        lastPoints = False
        vertexes = []
        for lat in range(lats):
            flat = -math.pi/2+ math.pi/(lats-1)*lat
            points = []

            for lon in range(lons):
                flon = -math.pi + 2*math.pi/(lons-1)*lon
                x = math.cos(flat)*math.cos(flon)
                y = math.cos(flat)*math.sin(flon)
                z = math.sin(flat)
                v = f(beta, numpy.array([x, y, z]))
                points.append(v)

            if lastPoints:
                l_lp = lastPoints[0]
                l_p = points[0]
                for i in range(1, len(points)):
                    lp = lastPoints[i]
                    p = points[i]
                    vertexes += [l_lp, l_p, p, lp]

                    l_lp = lp
                    l_p = p
            
            lastPoints = points

        super(Spherical, self).__init__(vertexes)

class Plane(Shape):
    def __init__(self, plane_fit, gridsize):
        plane = numpy.array(plane_fit)

        origin = -plane / numpy.dot(plane, plane)
        n = numpy.array([plane[1], plane[2], plane[0]])

        u = numpy.cross(plane, n)
        v = numpy.cross(plane, u)

        u /= numpy.linalg.norm(u)
        v /= numpy.linalg.norm(v)

        def project_point(point):
            return origin + point[0]*u + point[1]*v

        vertexes = []

        for x in range(-gridsize+1, gridsize):
            for y in range(-gridsize+1, gridsize):
                vertexes += [project_point((x-1, y-1)),
                             project_point((x, y-1)),
                             project_point((x, y)),
                             project_point((x-1, y))]

        super(self, Plane).__init__(vertexes)


class CompassCalibrationPlot():
    default_radius = 30
    def __init__(self):
        self.unit_sphere = Spherical([0, 0, 0], lambda beta, x: x, 32, 16)
        self.mag_sphere = False
        self.mag_cal_sphere = [0, 0, 0, 30]

        self.userscale = .02
        self.accel = [0, 0, 0]
        self.points = []
        self.sigmapoints = False
        self.apoints = []
        self.vpoints = []
        self.avg = [0, 0, 0]
        self.mode = GL_LINE
        self.uncalibrated_view = True

        '''
    if len(apoints) > 0:
        avg = [0, 0, 0]
        for j in range(3):
            for i in range(len(apoints)):
                avg[j] += apoints[i][j]
        avg = avg/numpy.linalg.norm(avg)
        alignment = quaternion.vec2vec2quat(avg, [0, 0, 1])
        print "avg accel", avg[0], avg[1], avg[2], "alignment", alignment, "angle", math.degrees(2*math.acos(alignment[0]))

    def fellipsoid(beta, x):
        return numpy.array([beta[3]*x[0] + beta[0], \
                            x[1]*beta[3]/beta[4] + beta[1], \
                            x[2]*beta[3]/beta[5] + beta[2]])
                            
    mag_ellipsoid = Shape(mag_cal_ellipsoid, fellipsoid,  64, 32);
    mag_plane = PlaneShape(mag_cal_plane, 2*int(mag_cal_sphere[3]))
    mag_plane_applied = PlaneShape(mag_cal_plane_applied, 2*int(mag_cal_sphere[3]))
    plane_norm = mag_cal_plane/numpy.linalg.norm(mag_cal_plane)

    print "plane norm", plane_norm
    alignment = quaternion.vec2vec2quat(plane_norm, [0, 0, 1])
    print "alignment", alignment, "angle", math.degrees(2*math.acos(alignment[0]))
    '''

    def read_data(self, msg):
        name, data = msg

        if name == 'imu/accel':
            self.accel = data['value']
        elif name == 'imu/compass':
            self.points.append(data['value'])
            if len(self.points) > 1000:
                self.points = self.points[1:]
        elif name == 'imu/compass_calibration_sigmapoints':
            self.sigmapoints = data['value']
        elif name == 'imu/compass_calibration' and data['value']:
            self.mag_cal_sphere = data['value'][0]

            def fsphere(beta, x):
                return beta[3]*x+beta[:3]
            self.mag_sphere = Spherical(self.mag_cal_sphere, fsphere,  64, 32);
        else:
            return False
        return True
        
    def display(self):
#        cal = mag_cal_sphere_bias + [30]
#        if mag_cal_sphere[3] > 20 and mag_cal_sphere[3] < 50:
        width, height = self.dim
        ar = float(width) / float(height)
        glViewport(0, 0, width, height)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glFrustum( -.1*ar, .1*ar, -.1, .1, .1, 15 )
        glMatrixMode(GL_MODELVIEW)

        cal = self.mag_cal_sphere

        glClearColor(0, 0, 0, 0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glPushMatrix()

        s = self.userscale

        glScalef(s, s, s)
        TranslateAfter( 0, 0, -1 )

        glPolygonMode(GL_FRONT_AND_BACK, self.mode)

        if self.uncalibrated_view:
            glPushMatrix()
            glTranslatef(-cal[0], -cal[1], -cal[2])

            if self.mag_sphere:
                glColor3f(0, 0, 1)
                self.mag_sphere.draw()

            glColor3f(1,0,0)

            glEnableClientState(GL_VERTEX_ARRAY);
            glVertexPointer(3, GL_FLOAT, 0, self.vpoints)
            glDrawArrays(GL_LINE_STRIP, 0, len(self.vpoints)/3)
            glDisableClientState(GL_VERTEX_ARRAY);
        
            glPointSize(2)
            glColor3f(1,0,0)
            glBegin(GL_POINTS)
            for i in range(len(self.points)-10):
                glVertex3fv(self.points[i])
            glEnd()
            
            glPointSize(4)
            glColor3f(0,1,0)
            glBegin(GL_POINTS)
            for i in range(max(len(self.points)-10, 0), len(self.points)):
                glVertex3fv(self.points[i])
            glEnd()
            
            glColor3f(0,1,1)
            glPointSize(6)
            glBegin(GL_POINTS)
            if self.sigmapoints:
                for p in self.sigmapoints:
                    glVertex3fv(p[:3])
            glEnd()

            glColor3f(1,9,1)
            glBegin(GL_LINES)
#            glVertex3fv(cal[:3])
            glVertex3fv(map(lambda x,y :-x*cal[3]+y, self.accel, cal[:3]))
            glVertex3fv(map(lambda x,y : x*cal[3]+y, self.accel, cal[:3]))
            glEnd()

            glPopMatrix()
        else: # calibrated view

            glColor3f(0, 1, 1)
            unit_sphere.draw()
            
            glColor3f(1,0,1)
            mag_plane_applied.draw()

            def f_apply_sphere(beta, x):
                return (x-beta[:3])/beta[3]
            cpoints = map(lambda p : f_apply_sphere(numpy.array(cal), numpy.array(p)), self.points)
            glColor3f(1,0,0)
            if cvpoints:
                glEnableClientState(GL_VERTEX_ARRAY);
                glVertexPointer(3, GL_FLOAT, 0, cvpoints)
                glDrawArrays(GL_LINE_STRIP, 0, len(cvpoints)/3)
                glDisableClientState(GL_VERTEX_ARRAY);

            glBegin(GL_LINE_STRIP)
            for i in range(len(cpoints)-10):
                glVertex3fv(cpoints[i])

            glColor3f(0,1,0)
            for i in range(max(len(cpoints)-10, 0), len(cpoints)):
                glVertex3fv(cpoints[i])
            glEnd()

            glBegin(GL_LINE_STRIP)
            glColor3f(1,1,0)
            for i in range(len(self.apoints)/10):
                glVertex3fv(self.apoints[10*i])
            glEnd()

        glPopMatrix()

    def special(self, key, x, y):
        step = 5
        if key == GLUT_KEY_UP:
            RotateAfter(step, 1, 0, 0)
        elif key == GLUT_KEY_DOWN:
            RotateAfter(step, -1, 0, 0)
        elif key == GLUT_KEY_LEFT:
            RotateAfter(step, 0, 1, 0)
        elif key == GLUT_KEY_RIGHT:
            RotateAfter(step, 0, -1, 0)
        elif key == GLUT_KEY_PAGE_UP:
            self.userscale /= .9
        elif key == GLUT_KEY_PAGE_DOWN:
            self.userscale *= .9
        elif key == GLUT_KEY_INSERT:
            RotateAfter(step, 0, 0, 1)

    def key(self, k, x, y):
        step = 5
        if k == '\b':
            RotateAfter(step, 0, 0, -1)
        elif k == '+' or k == '=':
            self.userscale /= .9
        elif k == '-' or k == '_':
            self.userscale *= .9
        elif k == 'f':
            glutFullScreen()
        elif k == 'm':
            if self.mode == GL_LINE:
                self.mode = GL_FILL
            else:
                self.mode = GL_LINE
        elif k == 'v':
            self.uncalibrated_view = not self.uncalibrated_view
        elif k == 27 or k=='q':
            exit(0)

    def reshape(self, width, height):
        glEnable(GL_DEPTH_TEST)
        self.dim = width, height
        

if __name__ == '__main__':
    host = ''
    if len(sys.argv) > 1:
        host = sys.argv[1]

    def on_con(client):
        watchlist = ['imu/accel', 'imu/compass', 'imu/compass_calibration', 'imu/compass_calibration_sigmapoints']
        for name in watchlist:
            client.watch(name)
        
    client = SignalKClient(on_con, host)
    plot = CompassCalibrationPlot()

    def display():
        plot.display()
        glutSwapBuffers()

    last = False
    def mouse(button, state, x, y):
        if button == GLUT_LEFT_BUTTON and state == GLUT_DOWN:
            global last
            last = x, y
                
    def motion(x, y):
        global last
        rotate_mouse(x - last[0], y - last[1])
        glutPostRedisplay()
        last = x, y

    n = 0
    def idle():
        while True:
            result = False
            if client:
                result = client.receive_single()

            if not result:
                time.sleep(.01)
                return

            if plot.read_data(result):
                glutPostRedisplay()


    glutInit(sys.argv)
    glutInitWindowPosition(0, 0)
    glutInitWindowSize(600, 500)
    glutInitDisplayMode(GLUT_DOUBLE | GLUT_RGB | GLUT_DEPTH)
    glutCreateWindow(sys.argv[0])

    glutIdleFunc(idle)
    glutReshapeFunc( plot.reshape )
    glutKeyboardFunc( lambda *a: apply(plot.key, a), glutPostRedisplay() )
    glutSpecialFunc( lambda *a : apply(plot.special, a), glutPostRedisplay() )
    glutDisplayFunc( display )

    glutMouseFunc( mouse )
    glutMotionFunc( motion )
    
    glutMainLoop()
