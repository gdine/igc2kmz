from cStringIO import StringIO
import datetime
import itertools
import math
import urllib
import sys
import time

import numpy
from pygooglechart import Axis, Chart, XYLineChart

from bounds import bounds, Bounds, BoundsSet
import kml
import kmz
import lib
from OpenStruct import OpenStruct


class Stock(object):

  def make_none_folder(self, visibility):
    icon = kml.Icon(href=self.pixel_url)
    overlay_xy = kml.overlayXY(x=0, y=0, xunits='fraction', yunits='fraction')
    screen_xy = kml.screenXY(x=0, y=0, xunits='fraction', yunits='fraction')
    size = kml.size(x=0, y=0, xunits='fraction', yunits='fraction')
    screen_overlay = kml.ScreenOverlay(icon, overlay_xy, screen_xy, size, visibility=visibility)
    folder = kml.Folder(screen_overlay, name='None', styleUrl=self.check_hide_children_style.url())
    return kmz.kmz(folder)

  def __init__(self):
    self.kmz = kmz.kmz()
    self.radio_folder_style = kml.Style(kml.ListStyle(listItemType='radioFolder'))
    self.kmz.add_roots(self.radio_folder_style)
    self.check_hide_children_style = kml.Style(kml.ListStyle(listItemType='checkHideChildren'))
    self.kmz.add_roots(self.check_hide_children_style)
    self.pixel_url = 'images/pixel.png'
    self.kmz.add_files({self.pixel_url: open(self.pixel_url).read()})
    self.visible_none_folder = self.make_none_folder(1)
    self.invisible_none_folder = self.make_none_folder(0)
    animation_icon_url = 'images/paraglider.png'
    self.animation_icon = kml.Icon(href=animation_icon_url)
    self.kmz.add_files({animation_icon_url: open(animation_icon_url).read()})


class Hints(object):

  def __init__(self):
    self.altitude_mode = 'absolute'
    self.color = 'ff0000ff'
    self.width = 2


class Track(object):

  def __init__(self, meta, times, coords):
    self.meta = meta
    self.times = times
    self.coords = coords
    self.bounds = BoundsSet()
    self.bounds.ele = Bounds(self.coords[0].ele)
    for coord in self.coords:
      self.bounds.ele.merge(coord.ele)
    self.bounds.time = Bounds(self.times[0], self.times[-1])
    self.elevation_data = self.bounds.ele.min != 0 or self.bounds.ele.max != 0

  def analyse(self, half_period=30):
    n = len(self.coords)
    def make_seq(seq, dtype=numpy.float):
      result = numpy.empty((len(seq) + 2,), dtype=dtype)
      result[0], result[-1] = seq[0], seq[-1]
      result[1:len(seq) + 1] = seq
      return result
    lat = make_seq([coord.lat for coord in self.coords])
    lat *= math.pi / 180.0
    sin_lat = numpy.sin(lat)
    cos_lat = numpy.cos(lat)
    lon = make_seq([coord.lon for coord in self.coords])
    lon *= math.pi / 180.0
    ds = 6371000.0 * numpy.arccos(sin_lat[1:-1] * sin_lat[0:-2] + cos_lat[1:-1] * cos_lat[0:-2] * numpy.cos(lon[1:-1] - lon[0:-2]))
    s = numpy.empty((n + 2,))
    s[0], s[-1] = 0.0, 0.0
    s[1:-2] = numpy.add.accumulate(ds)
    t = make_seq(self.coords.t, dtype=numpy.int)
    t[0], t[-1] = 0, sys.maxint
    j, k = 0, 1
    left_index, right_index = numpy.empty((n,), dtype=numpy.int), numpy.empty((n,), dtype=numpy.int)
    left_k, right_k = numpy.empty((n,)), numpy.empty((n,))
    for i in xrange(0, n):
      while t[j + 1] <= t[i + 1] - half_period:
        j += 1
      while t[k + 1] <= t[i + 1] + half_period:
        k += 1
      left_index[i], right_index[i] = j, k
      left_k[i] = float(t[j + 1] - t[i + 1] + half_period) / (t[j + 1] - t[j])
      right_k[i] = float(t[k] - t[i + 1] - half_period) / (t[k + 1] - t[k])
    s_left = s[left_index] * left_k + s[left_index + 1] * (1.0 - left_k)
    s_right = s[right_index] * right_k + s[right_index + 1] * (1.0 - right_k)
    self.speed = (s_right - s_left) / (2 * half_period)
    self.bounds.speed = bounds(self.speed.__iter__())
    if self.elevation_data:
      ele = make_seq([coord.ele for coord in self.coords])
      self.ele = (ele[1:-1] + ele[0:-2]) / 2.0
      dz = ele[1:-1] - ele[0:-2]
      self.max_dz_positive = max(numpy.add.accumulate(dz))
      self.total_dz_positive = numpy.sum(x for x in dz if x > 0.0)
      z_left = ele[left_index] * left_k + ele[left_index + 1] * (1.0 - left_k)
      z_right = ele[right_index] * right_k + ele[right_index + 1] * (1.0 - right_k)
      self.climb = (z_right - z_left) / (2 * half_period)
      self.bounds.climb = bounds(self.climb.__iter__())

  def make_solid_track(self, hints, style, altitude_mode, extrude=None, **folder_options):
    line_string = kml.LineString(coordinates=self.coords, altitudeMode=altitude_mode)
    if extrude:
      line_string.add(extrude=1)
    placemark = kml.Placemark(style, line_string)
    folder_options['styleUrl'] = hints.globals.stock.check_hide_children_style.url()
    return kmz.kmz(kml.Folder(placemark, **folder_options))

  def make_colored_track(self, hints, values, scale, altitude_mode, **folder_options):
    folder = kml.Folder(name='Colored by %s' % scale.title, styleUrl=hints.globals.stock.check_hide_children_style.url(), **folder_options)
    styles = [kml.Style(kml.LineStyle(color=color, width=hints.width)) for color in scale.colors()]
    discrete_values = map(scale.discretize, values)
    for start, end in lib.runs(discrete_values):
      line_string = kml.LineString(coordinates=self.coords[start:end + 1], altitudeMode=hints.altitude_mode)
      style_url = kml.styleUrl(styles[discrete_values[start]].url())
      placemark = kml.Placemark(style_url, line_string)
      folder.add(placemark)
    return kmz.kmz(folder).add_roots(*styles)

  def make_track_folder(self, hints):
    folder = kmz.kmz(kml.Folder(name='Track', open=1, styleUrl=hints.globals.stock.radio_folder_style.url()))
    folder.add(hints.globals.stock.invisible_none_folder)
    if self.elevation_data:
      folder.add(self.make_colored_track(hints, map(lambda c: c.ele, self.coords), hints.globals.altitude_scale, 'absolute', visibility=0))
      folder.add(self.make_colored_track(hints, self.climb, hints.globals.climb_scale, 'absolute'))
    folder.add(self.make_colored_track(hints, self.speed, hints.globals.speed_scale, hints.altitude_mode))
    folder.add(self.make_solid_track(hints, kml.Style(kml.LineStyle(color=hints.color, width=hints.width)), hints.altitude_mode, name='Solid color', visibility=0))
    return folder

  def make_shadow_folder(self, hints):
    if not self.elevation_data:
      return kmz.kmz()
    folder = kmz.kmz(kml.Folder(name='Shadow', open=1, styleUrl=hints.globals.stock.radio_folder_style.url()))
    folder.add(hints.globals.stock.invisible_none_folder)
    folder.add(self.make_solid_track(hints, kml.Style(kml.LineStyle(color='ff000000', width=1)), 'clampToGround', name='Normal', visibility=1))
    folder.add(self.make_solid_track(hints, kml.Style(kml.LineStyle(color='00000000', width=1), kml.PolyStyle(color='80000000')), 'absolute', True, name='Extrude', visibility=0))
    folder.add(self.make_solid_track(hints, kml.Style(kml.LineStyle(color=hints.color, width=hints.width)), 'clampToGround', name='Solid color', visibility=0))
    return folder

  def make_animation(self, hints):
    style = kml.Style(kml.IconStyle(hints.globals.stock.animation_icon, color=hints.color, scale=0.5))
    folder = kml.Folder(style, name='Animation', open=0, styleUrl=hints.globals.stock.check_hide_children_style.url())
    point = kml.Point(coordinates=[self.coords[0]], altitudeMode=hints.altitude_mode)
    timespan = kml.TimeSpan(end=kml.dateTime(self.times[0]))
    placemark = kml.Placemark(point, timespan, styleUrl=style.url())
    folder.add(placemark)
    for i in xrange(1, len(self.coords)):
      point = kml.Point(coordinates=[self.coords[i - 1].halfway_to(self.coords[i])], altitudeMode=hints.altitude_mode)
      timespan = kml.TimeSpan(begin=kml.dateTime(self.times[i - 1]), end=kml.dateTime(self.times[i]))
      placemark = kml.Placemark(point, timespan, styleUrl=style.url())
      folder.add(placemark)
    point = kml.Point(coordinates=[self.coords[-1]], altitudeMode=hints.altitude_mode)
    timespan = kml.TimeSpan(begin=kml.dateTime(self.times[-1]))
    placemark = kml.Placemark(point, timespan, styleUrl=style.url())
    folder.add(placemark)
    return kmz.kmz(folder)

  def make_graph(self, hints, values, scale, epsilon):
    chart = XYLineChart(hints.globals.graph_width, hints.globals.graph_height, x_range=hints.globals.time_scale.range, y_range=scale.range)
    chart.fill_solid(Chart.BACKGROUND, 'ffffff00')
    chart.fill_solid(Chart.CHART, 'ffffffcc')
    axis_index = chart.set_axis_labels(Axis.BOTTOM, hints.globals.time_scale.labels)
    chart.set_axis_positions(axis_index, hints.globals.time_scale.positions)
    chart.set_axis_style(axis_index, 'ffffff')
    axis_index = chart.set_axis_range(Axis.LEFT, scale.range[0], scale.range[1])
    chart.set_axis_style(axis_index, 'ffffff')
    chart.set_grid(hints.globals.time_scale.grid_step, scale.grid_step, 2, 2)
    indexes = lib.douglas_peucker(self.coords.t, values, epsilon)
    chart.add_data([self.coords.t[i] for i in indexes])
    chart.add_data([values[i] for i in indexes])
    print chart.get_url()
    icon = kml.Icon(href=kml.CDATA(chart.get_url()))
    overlay_xy = kml.overlayXY(x=0, y=0, xunits='fraction', yunits='fraction')
    screen_xy = kml.screenXY(x=0, y=16, xunits='fraction', yunits='pixels')
    size = kml.size(x=0, y=0, xunits='fraction', yunits='fraction')
    screen_overlay = kml.ScreenOverlay(icon, overlay_xy, screen_xy, size)
    folder = kml.Folder(screen_overlay, name=scale.title.capitalize(), styleUrl=hints.globals.stock.check_hide_children_style.url(), visibility=0)
    return folder

  def make_graphs_folder(self, hints):
    folder = kmz.kmz(kml.Folder(name='Graphs', open=1, styleUrl=hints.globals.stock.radio_folder_style.url()))
    folder.add(hints.globals.stock.visible_none_folder)
    folder.add(self.make_graph(hints, self.ele, hints.globals.altitude_scale, 5))
    folder.add(self.make_graph(hints, self.climb, hints.globals.climb_scale, 0.1))
    return folder

  def kmz(self, hints):
    folder = kmz.kmz(kml.Folder(name=self.meta.name, open=1))
    rows = []
    if self.meta.pilot_name:
      rows.append(('Pilot name', self.meta.pilot_name))
    if self.meta.glider_type:
      rows.append(('Glider type', self.meta.glider_type))
    if self.meta.glider_id:
      rows.append(('Glider ID', self.meta.glider_id))
    rows.append(('Take-off time', (self.times[0] + hints.globals.timezone_offset).strftime('%H:%M:%S')))
    rows.append(('Landing time', (self.times[-1] + hints.globals.timezone_offset).strftime('%H:%M:%S')))
    hour, seconds = divmod((self.times[-1] - self.times[0]).seconds, 3600)
    minute, second = divmod(seconds, 60)
    rows.append(('Duration', '%d:%02d:%02d' % (hour, minute, second)))
    #rows.append(('Track length', '%.3fkm' % (self.s[-1] / 1000.0)))
    if self.elevation_data:
      rows.append(('Take-off altitude', '%dm' % self.coords[0].ele))
      rows.append(('Maximum altitude', '%dm' % self.bounds.ele.max))
      rows.append(('Minimum altitude', '%dm' % self.bounds.ele.min))
      rows.append(('Landing altitude', '%dm' % self.coords[-1].ele))
      rows.append(('Accumulated altitude gain', '%dm' % self.total_dz_positive))
      rows.append(('Maximum altitude gain', '%dm' % self.max_dz_positive))
      rows.append(('Maximum climb', '%.1fm/s' % self.bounds.climb.max))
      rows.append(('Maximum sink', '%.1fm/s' % self.bounds.climb.min))
    rows.append(('Maximum speed', '%.1fm/s' % self.bounds.speed.max))
    folder.add(kml.description(kml.CDATA('<table>%s</table>' % ''.join('<tr><th align="right">%s</th><td>%s</td></tr>' % row for row in rows))))
    snippet = [self.meta.pilot_name, self.meta.glider_type, (self.times[0] + hints.globals.timezone_offset).strftime('%Y-%m-%d')]
    folder.add(kml.Snippet(', '.join(s for s in snippet if s)))
    folder.add(self.make_animation(hints))
    folder.add(self.make_track_folder(hints))
    folder.add(self.make_shadow_folder(hints))
    folder.add(self.make_graphs_folder(hints))
    return folder
