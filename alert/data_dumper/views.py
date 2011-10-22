# This software and any associated files are copyright 2010 Brian Carver and
# Michael Lissner.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import calendar
import gzip
import os

from alert.alertSystem.models import Court
from alert.alertSystem.models import Document
from alert.lib.db_tools import queryset_iterator
from alert.settings import DUMP_DIR

from django.http import HttpResponseBadRequest
from django.http import HttpResponseRedirect
from django.shortcuts import render_to_response
from django.template import RequestContext

from datetime import date
from lxml import etree


class myGzipFile(gzip.GzipFile):
    '''Backports Python 2.7 functionality into 2.6.

    In order to use the 'with syntax' below, I need to subclass the gzip
    library here. Once all of the machines are running Python 2.7, this class
    can be removed, and the 'with' code below can simply reference the gzip
    class rather than this one.

    This line of code worked in 2.7:
    with gzip.open(filename, mode='wb') as z_file:
    '''
    def __enter__(self):
        if self.fileobj is None:
            raise ValueError("I/O operation on closed GzipFile object")
        return self

    def __exit__(self, *args):
        self.close()


def dump_index(request):
    '''Shows an index page for the dumps.'''
    courts = Court.objects.all().order_by('startDate')
    return render_to_response('dumps/dumps.html', {'courts' : courts},
        RequestContext(request))


def get_date_range(year, month, day):
    ''' Create a date range to be queried.

    Given a year and optionally a month or day, return a date range. If only a
    year is given, return start date of January 1, and end date of December
    31st. Do similarly if a year and month are supplied or if all three values
    are provided.
    '''
    # Sort out the start dates
    if month == None:
        start_month = 1
    else:
        start_month = int(month)
    if day == None:
        start_day = 1
    else:
        start_day = int(day)

    start_year = int(year)
    start_date = '%d-%02d-%02d' % (start_year, start_month, start_day)

    annual = False
    monthly = False
    daily = False
    # Sort out the end dates
    if day == None and month == None:
        # it's an annual query
        annual = True
        end_month = 12
        end_day = 31
    elif day == None:
        # it's a month query
        monthly = True
        end_month = int(month)
        end_day = calendar.monthrange(int(year), end_month)[1]
    else:
        # all three values provided!
        daily = True
        end_month = int(month)
        end_day = int(day)

    end_year = int(year)
    end_date = '%d-%02d-%02d' % (end_year, end_month, end_day)

    return start_date, end_date, annual, monthly, daily


def serve_or_gen_dump(request, court, year = None, month = None, day = None):
    if year is None:
        if court != 'all':
            # Sanity check
            return HttpResponseBadRequest('<h2>Error 400: Complete dumps are \
                not available for individual courts.</h2>')
        else:
            # Serve the dump for all cases.
            today = date.today()
            end_date = '%d-%02d-%02d' % (today.year, today.month, today.day)
            all_dates = True

    else:
        # Date-based dump
        all_dates = False
        start_date, end_date, annual, monthly, daily = get_date_range(
            year, month, day)

        # Ensure that it's a valid request.
        today = date.today()
        today_str = '%d-%02d-%02d' % (today.year, today.month, today.day)
        if (today_str < end_date) and (today_str < start_date):
            # It's the future. They fail.
            return HttpResponseBadRequest('<h2>Error 400: Requested date is in\
                the future. Please try again later.</h2>')
        elif today_str <= end_date:
            # Some of the data is in the past, some could be in the future.
            return HttpResponseBadRequest('<h2>Error 400: Requested date is \
                partially in the future. Please try again later.</h2>')

    filename = court + '.xml.gz'
    if all_dates:
        filepath = ''
    elif daily:
        filepath = os.path.join(year, month, day)
    elif monthly:
        filepath = os.path.join(year, month)
    elif annual:
        filepath = os.path.join(year)

    # See if we already have it cached.
    try:
        _ = open(os.path.join(DUMP_DIR, filepath, filename), 'rb')
        return HttpResponseRedirect(os.path.join('/dumps', filepath, filename))

    except IOError:
        # We don't have it cached on disk. Make it, save it and redirect to it.
        if all_dates:
            docs_to_dump = queryset_iterator(Document.objects.filter(
                dateFiled__lte = end_date))
        else:
            # Time-based dump
            if court == 'all':
                # dump everything.
                docs_to_dump = queryset_iterator(Document.objects.filter(
                    dateFiled__gte = start_date, dateFiled__lte = end_date))
            else:
                # dump just the requested court
                docs_to_dump = queryset_iterator(Document.objects.filter(
                   dateFiled__gte = start_date, dateFiled__lte = end_date,
                   court = court))

        # This var is needed to clear out null characters and control characters
        # (skipping newlines)
        null_map = dict.fromkeys(range(0, 10) + range(11, 13) + range(14, 32))

        try:
            os.makedirs(os.path.join(DUMP_DIR, filepath))
        except OSError:
            # Path exists.
            pass

        os.chdir(DUMP_DIR)
        with myGzipFile(os.path.join(filename), mode = 'wb') as z_file:
            z_file.write('<?xml version="1.0" encoding="utf-8"?>\n' +
                         '<opinions dumpdate="' + str(date.today()) + '">\n')

            try:
                for doc in docs_to_dump:
                    row = etree.Element("opinion")
                    try:
                        # These are required by the DB, and thus are safe
                        # without the try/except blocks
                        row.set('id', str(doc.documentUUID))
                        row.set('sha1', doc.documentSHA1)
                        row.set('court', doc.court.get_courtUUID_display())
                        row.set('download_URL', doc.download_URL)
                        row.set('time_retrieved', str(doc.time_retrieved))
                        try:
                            row.set('dateFiled', str(doc.dateFiled))
                        except:
                            # Value not found.
                            row.set('dateFiled', '')
                        try:
                            row.set('precedentialStatus', doc.documentType)
                        except:
                            # Value not found.
                            row.set('precedentialStatus', '')
                        try:
                            row.set('local_path', str(doc.local_path))
                        except:
                            # Value not found.
                            row.set('local_path', '')
                        try:
                            row.set('docketNumber', doc.citation.docketNumber)
                        except:
                            # Value not found.
                            row.set('docketNumber', '')
                        try:
                            row.set('westCite', doc.citation.westCite)
                        except:
                            # Value not found.
                            row.set('westCite', '')
                        try:
                            row.set('lexisCite', doc.citation.lexisCite)
                        except:
                            # Value not found.
                            row.set('lexisCite', '')
                        try:
                            row.set('caseNameFull', doc.citation.caseNameFull)
                        except:
                            # Value not found.
                            row.set('caseNameFull', '')
                        try:
                            row.set('source', doc.get_source_display())
                        except:
                            # Value not found.
                            row.set('source', '')

                        # Gather the doc text
                        if doc.documentHTML != '':
                            row.text = doc.documentHTML
                        else:
                            row.text = doc.documentPlainText.translate(null_map)
                    except ValueError:
                        # Null byte found. Punt.
                        continue

                    z_file.write('  %s\n' % etree.tostring(row).encode('utf-8'))

            except IndexError:
                return HttpResponseBadRequest('<h2>Error 400: No cases found \
                    for this time period.</h2>')


            # Close things off
            z_file.write('</opinions>')

        # Move the file from our of the working directory and into it's resting
        # place.
        os.rename(os.path.join(filename),
                  os.path.join(DUMP_DIR, filepath, filename))

        return HttpResponseRedirect(os.path.join('/dumps', filepath, filename))