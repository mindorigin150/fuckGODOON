"""Interpolate the saved WGS84 track and produce the mini-program's GCJ-02 points."""
import math


def distance(a, b):
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlat, dlon = lat2 - lat1, math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 12742000 * math.asin(min(1, math.sqrt(h)))


def gcj02(lat, lon):
    x, y = lon - 105, lat - 35
    dlat = -100 + 2*x + 3*y + .2*y*y + .1*x*y + .2*math.sqrt(abs(x))
    dlat += (20*math.sin(6*x*math.pi) + 20*math.sin(2*x*math.pi))*2/3
    dlat += (20*math.sin(y*math.pi) + 40*math.sin(y/3*math.pi))*2/3
    dlat += (160*math.sin(y/12*math.pi) + 320*math.sin(y*math.pi/30))*2/3
    dlon = 300 + x + 2*y + .1*x*x + .1*x*y + .1*math.sqrt(abs(x))
    dlon += (20*math.sin(6*x*math.pi) + 20*math.sin(2*x*math.pi))*2/3
    dlon += (20*math.sin(x*math.pi) + 40*math.sin(x/3*math.pi))*2/3
    dlon += (150*math.sin(x/12*math.pi) + 300*math.sin(x/30*math.pi))*2/3
    rad = math.radians(lat)
    magic = 1 - .00669342162296594323 * math.sin(rad)**2
    root = math.sqrt(magic)
    dlat = dlat * 180 / ((6378245*(1-.00669342162296594323))/(magic*root)*math.pi)
    dlon = dlon * 180 / (6378245/root*math.cos(rad)*math.pi)
    return lat + dlat, lon + dlon


class Track:
    """Walk a closed WGS84 polyline at the configured speed."""
    def __init__(self, points):
        self.points = points
        self.lengths = [distance(p, points[(i+1) % len(points)]) for i,p in enumerate(points)]
        self.length = sum(self.lengths)
        if self.length <= 0:
            raise ValueError('Track must have positive length')

    def position(self, meters):
        offset = meters % self.length
        for i, length in enumerate(self.lengths):
            if offset < length:
                a, b = self.points[i], self.points[(i+1) % len(self.points)]
                f = offset / length
                return gcj02(a[0] + (b[0]-a[0])*f, a[1] + (b[1]-a[1])*f)
            offset -= length
        raise ArithmeticError('Track interpolation exceeded its perimeter')
