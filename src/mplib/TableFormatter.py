"""Formatter of plain-text tabular data.
Given optional (but common) column headers and rows of cells, produces a nicely formatted text output.
Rows that are not cells are treated as strings and printed across all columns.

Copyright (C) 2011-2019 Doug Lee

This program is free software: you can redistribute it and/or modify it
under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 3 of the License, or (at your
option) any later version.

This program is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
for more details.

You should have received a copy of the GNU General Public License along
with this program.  If not, see <http://www.gnu.org/licenses/>.

"""

class TableFormatter(object):
	"""Usage:
		tbl = TableFormatter(title[, colHeaders])
		tbl.addRow(...)  # each a row of cells or a string to print across all columns)
		print tbl.format([gutterWidth])
	"""
	def __init__(self, title="", colheaders=[]):
		self.title = title
		self.colheaders = colheaders
		self.rows = []
		self.rowcount = 0

	def _isList(self, x):
		return isinstance(x, (list, tuple))

	def addRow(self, row, excludeFromCount=False):
		self.rows.append(row)
		# Exclude row from count when requested and also when it's not a list--i.e., it prints ignoring columns.
		if not excludeFromCount and self._isList(row):
			self.rowcount += 1

	def format(self, gutterwidth=2):
		"""Print the table with the given gutter width (space count) between columns, default 2.
		If gutterwidth is set to 0, columns are separated by tabs.
		If title is given and not null, it appears above the table with a row count; and the table indents under it.
		Otherwise the table prints flush left.
		Rows that are not cells are treated as strings and printed across all columns.
		If you want one of these to indent, include indent spaces and such in the string when calling addRow().
		"""
		if not len(self.rows):
			if not self.title: return ""
			return self.title +":  0"
		if self.colheaders:
			widths = [len(str(hdr)) for hdr in self.colheaders]
		elif len(self.rows):
			widths = [len(str(cell)) for cell in self.rows[0]]
		for row in self.rows:
			# Skip rows that have no columns.
			if not self._isList(row): continue
			for i,cell in enumerate(row):
				widths[i] = max(widths[i], len(str(cell)))
		tabs = True
		gutter = "\t"
		if gutterwidth:
			tabs = False
			gutter = " " * gutterwidth
		result = ""
		lmargin = ""
		if self.title:
			result = "%s (%d):\n" % (self.title, self.rowcount)
			lmargin = "    "
		allRows = []
		if self.colheaders: allRows.append(self.colheaders)
		allRows.extend(self.rows)
		for row in allRows:
			if not self._isList(row):
				result += lmargin +"    " +row +"\n"
				continue
			fields = []
			for i,cell in enumerate(row):
				if tabs: fmt = "%s"
				else: fmt = "%-" +str(widths[i]) +"s"
				fields.append( fmt % (str(cell)))
			result += lmargin +gutter.join(fields) +"\n"
		return result

