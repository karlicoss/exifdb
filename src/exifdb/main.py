from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cached_property
from pathlib import Path
import re
import shutil
import subprocess
import sqlite3
import sys
from typing import Iterator, Generator, Callable, Protocol, Final

import click
import geopy  # type: ignore
import orjson
import pytz
from sqlalchemy import (
    create_engine,
    MetaData, Table, Column, String, Computed,
    text,
)
from sqlalchemy.engine import Row as SARow


from exifdb.common import logger, get_media_mtime
from exifdb.config import Config
from exifdb.tags import (
    Tags,
    DT_TAGS, DT_EXTRA,
    TZ_TAGS, TZ_EXTRA,
    GPS_TAGS,
    TAGS_WITH_COLON,
)
from exifdb.utils import timezone_finder


ROOT_DIR = Path(__file__).parent.parent.parent

BAK_DIR = ROOT_DIR / 'backups'
assert BAK_DIR.exists(), BAK_DIR

EXIFTOOL_LOG = ROOT_DIR / 'exiftool.log'
EXIFTOOL_LOG.touch()


class Db:
    def __init__(self, *, db_path: str) -> None:
        echo = False
        creator = lambda: sqlite3.connect(f'file:{db_path}?immutable=1', uri=True)
        self.engine = create_engine('sqlite://', creator=creator, echo=echo)
        # TODO should it be ctx manager? not sure
        # TODO start with read only

    # todo query
    def query(self, *, where: str | None = None) -> Iterator[SARow]:
        wh = '' if where is None else f' WHERE {where}'
        with self.engine.connect() as conn:
            yield from conn.execute(text(f'SELECT * FROM data {wh}'))


@dataclass
class Row:
    row: SARow

    @cached_property
    def path(self) -> str:
        # TODO return Path maybe?
        return self.row.path

    @cached_property
    def mtime(self) -> str:
        return self.row.mtime

    @cached_property
    def exif(self) -> dict[str, str]:
        return orjson.loads(self.row.exif)

    def get(self, tag: str) -> str | None:
        return getattr(self.row, tag)


def exiftool_set_tag(*, path: str, tag: str, value: str, comment: str) -> None:
    assert '.jpg' in path, path  # uhh, UserComment won't work otherwise?

    bak_name = Path(path).name + '.bak.' + datetime.now().strftime('%Y%m%d%H%M%S')
    logger.debug(f'backing up {path} to {BAK_DIR / bak_name}')
    shutil.copy2(path, BAK_DIR / bak_name)

    # TODO add current date??
    cmds = [
        ['exiftool', '-q', '-overwrite_original', f'-{tag}={value}', path, ],
        # ugh. if UserComment didn't exist before, that prints a warning. kinda annoying
        # but we also don't want to ignore warnings for the command above
        # so have to do in two commands..
        ['exiftool', '-q', '-overwrite_original', '-m', '-UserComment<$UserComment' + '\n' + comment, path],
        # and since we do two commands, can't rely on builtin _original preserving mechanism.. so copy manually
    ]

    for cmd in cmds:
        logger.debug(f'running {cmd}')
        # write to the operations log so it's easier to figure out what happened just in case
        with EXIFTOOL_LOG.open('a') as fo:
            fo.write(f'running {cmd}\n')

        subprocess.check_call(cmd)


datetime_aware = datetime
datetime_naive = datetime
Coordinate = tuple[str, str]  # lat, lon


@dataclass
class Parsed:
    coordinate: Coordinate | None = None
    gps_datetime: datetime_aware | None = None
    filename_datetime: datetime_naive | None = None


Error = str

_SEEN = set(TAGS_WITH_COLON)


class Fix(Protocol):
    @property
    def description(self) -> str: ...

    def fix(self) -> None: ...


def check_media(row: Row) -> Iterator[
        # in simple cases just return the error, otherwise attach a fix
        Error | tuple[Error, Fix],
]:
    path = row.path
    ppath = Path(path)
    filename = ppath.name

    current_mtime = get_media_mtime(ppath)
    db_mtime = row.mtime

    if current_mtime != db_mtime:
        yield f"file mtime {current_mtime} and db mtime {db_mtime} are different! please update the cache"
        return

    exif = row.exif
    mime = row.get('MIMEType')


    # todo maybe detect if a file doesn't look like a photo at all?
    # e.g. instagram saves etc
    # although gonna be tricky and we might want to enrich such pics as well

    # todo not sure if really belongs here?
    def detect_datetimeish_tags() -> None:
        '''
        try to extract anything that looks like timestamps
        '''
        for k, v in exif.items():
            if k in _SEEN:
                continue
            if ':' not in str(v):
                # also checked '-' but doesn't look like there are any YYYY-mm-dd dates in exif
                continue
            _SEEN.add(k)
            logger.warning(f'{path} : tag {k} with value {v} contains a colon, might be worth adding it to tags.py')

    detect_datetimeish_tags()

    def check_coordinate() -> Generator[
            Error,
            None,
            Coordinate | None,
    ]:
        coords = {tag: row.get(tag) for tag in GPS_TAGS}
        if all(c is not None for c in coords.values()):
            lat_s = row.get(Tags.GPSLatitude); assert lat_s is not None
            lon_s = row.get(Tags.GPSLongitude); assert lon_s is not None
            gps_re = r'\d+.*(N|S|W|E)'
            if re.fullmatch(gps_re, lat_s) and re.fullmatch(gps_re, lon_s):
                return (lat_s, lon_s)
            else:
                # never happened but just in case
                yield f'bad gps coordinates, likely missing ref {exif}'
        elif all(c is None for c in coords.values()):
            yield 'missing GPSPosition'
        else:
            # just in case, but didn't happen for me
            yield f'bad gps coordinate tags {exif}'
        return None

    # hmm not sure about Generator usage here.. but I guess ok to experiment
    # https://peps.python.org/pep-0380/
    coordinate: Final[Coordinate | None] = yield from check_coordinate()

    def check_gps_datetime() -> Generator[
            Error,
            None,
            datetime_aware | None,
    ]:
        if mime in {'video/mp4', 'video/quicktime', 'video/x-msvideo'}:
            # seems like quicktime doesn't support gps datetime
            # https://exiftool.org/TagNames/QuickTime.html
            return None

        gps_dt_s = row.get(Tags.GPSDateTime)
        if gps_dt_s is None:
            # todo not sure if should be error tbh, tons of photos don't have it...
            # maybe a warning?
            yield 'missing GPSDateTime'
            return None

        try:
            gps_dt = datetime.strptime(gps_dt_s, '%Y:%m:%d %H:%M:%SZ')
        except Exception as e:
            # TODO offer suggested fix?
            yield f'bad GPS datetime (likely no timezone) {gps_dt_s}'
            return None

        gps_dt = gps_dt.replace(tzinfo=timezone.utc)
        return gps_dt

    gps_datetime: Final[datetime_aware | None] = yield from check_gps_datetime()

    def check_filename_datetime() -> Generator[
            Error,
            None,
            # bool indicates whether it has microseconds
            tuple[datetime_naive, bool] | None,
    ]:
        pp = Path(path)
        name = pp.name.replace(''.join(pp.suffixes), '')  # split suffixes (incl multiple)
        timestamp = r'(\d{8})_(\d{9}|\d{6}(_\d{3})?)'
        match = re.match(r'(?:[\w\s]+_)?' + timestamp, name)
        if match is None:
            # TODO this might end up a bit spammy..
            # e.g. filename might be weird, but we might have all tags etc
            # I guess later when we return Parsed object, we could filter them out?
            yield f"couldn't extract timestamp from the filename {name}"
            return None
        ds = match.group(1)
        ts = match.group(2).replace('_', '')  # sometimes millis are separated with a _
        has_subsec = len(ts) == 9
        ts = ts + '0' * (12 - len(ts))  # want to pad for microsecond format parsing

        try:
            dt = datetime.strptime(ds + ts, '%Y%m%d%H%M%S%f')
        except Exception as e:
            yield f"couldn't parse timestamp from the filename {name}"
            return None

        # TODO sanity check here as well? maybe share with other timestamps
        return (dt, has_subsec)

    filename_datetime_res = yield from check_filename_datetime()

    def check_original_datetime() -> Generator[
            Error,
            None,
            # todo not sure if naive or aware? depends..
            datetime | None,
    ]:
        if mime == 'video/mp4':
            # NOTE hmm so in quicktime (mp4) format, CreateDate supposed to be UTC?
            # (see mvhd header docs)
            # here https://developer.apple.com/library/archive/documentation/QuickTime/QTFF/QTFFChap2/qtff2.html
            #
            # digikam does some magic, basically it depends on the camera
            # https://github.com/KDE/digikam/commit/c938087700c20dc3f129444e455ad1596c365063
            # https://www.mail-archive.com/search?l=kde-bugs-dist@kde.org&q=subject:%22%5C%5Bdigikam%5C%5D+%5C%5BBug+432369%5C%5D+Wrong+time+for+mp4+in+_almost_+all+places%2C+metadata+differs+from+filesystem%5C%2Fother+apps.+DB+issues%5C%3F%22&o=newest&f=1
            # but seems like this is the case for my android videos
            #
            # https://exiftool.org/TagNames/QuickTime.html
            # > According to the specification, integer-format QuickTime date/time tags should be stored as UTC.
            # > Unfortunately, digital cameras often store local time values instead (presumably because they don't know the time zone). For this reason, by default ExifTool does not assume a time zone for these values.
            # > However, if the API QuickTimeUTC option is set, then ExifTool will assume these values are properly stored as UTC, and will convert them to local time when extracting.
            #
            # uhh.. I think I need to do something similar to what digikam does, and then just use that QuickTimeUTC api?
            # or rely on reconciliation of inferring datetime from filename to double check

            # also, seems that mp4 always has it, but sometimes it's zeroed out (e.g. whatsapp videos)
            dt_orig_s = row.get(Tags.CreateDate)
        elif mime == 'video/quicktime':
            # TODO these actually have CreationDate that has timezone?
            # but unclear -- seems it's derived from this header and basically free form?
            # not sure if any tools really use it?
            # https://developer.apple.com/library/archive/documentation/QuickTime/QTFF/Metadata/Metadata.html#//apple_ref/doc/uid/TP40000939-CH1-SW43
            # yeah, e.g. ffprobe prints
            # TAG:com.apple.quicktime.creationdate=2022-08-18T10:03:44+0100
            # 1. need to prefer that and maybe set timezone after extraction
            # 2. wonder if possible to retrofit that for video/mp4 files?
            dt_orig_s = row.get(Tags.CreateDate)
        else:
            # this works for jpg
            # works for heic (iphone photos?)
            # seems to work for avi as well -- it uses some sort of RIFF metadata which maps into DateTimeOriginal
            dt_orig_s = row.get(Tags.DateTimeOriginal)
            # note: some software (e.g. digikam does a bunch of fallbacks)
            # e.g. DateTimeOriginal, then CreateDate, then ModifyDate
            # imo it's better to be a bit more specific and fix up metadata to have canonical tags
            # that way photos would be more compatible with software that only uses most common tags

        if dt_orig_s is None:
            # ok, it's up to the downstream user to interpret this and suggest to fix?
            # not sure if should emit error here?
            return None

        # sometimes parsing fails
        # a couple cases I saw were:
        # 0000:00:00 00:00:00 as timestamp
        # 24 as the hour
        try:
            res = datetime.strptime(dt_orig_s, '%Y:%m:%d %H:%M:%S')
        except Exception as e:
            # todo add tag name?
            yield f"couldn't parse {dt_orig_s}"
            return None

        # just in case
        if not (datetime(1900, 1, 1, 0, 0, 0) < res < datetime(2100, 1, 1, 0, 0, 0)):
            yield f'bad date {dt_orig_s}'
            return None

        return res

    dt_orig_res: Final[datetime_naive | None] = yield from check_original_datetime()

    # ok, so apparently generally photo software isn't relying on GPS tags to infer datetime
    # so should try some other tags first?

    # TODO a couple of photos have MetadataDate but nothing else
    # TODO this should be moved inside check_original_datetime?
    if dt_orig_res is None:
        # ok, let's see if anything else we could do
        dt_tags: dict[str, str | None] = {
            **{t: row.get(t) for t in DT_TAGS},
            **{t: exif.get(t) for t in DT_EXTRA},
        }
        notnone = {k: v for k, v in dt_tags.items() if v is not None}
        # OK, so maybe to start with -- just print stuff above as a suggestion
        xxerr = f'missing created datetime'
        if len(notnone) > 0:
            xxerr += f', also found some datetime-like tags {notnone}'
        if filename_datetime_res is not None:
            filename_datetime, filename_datetime_has_subsec = filename_datetime_res
            # TODO only offer fix if mime is jpeg?
            dtfmt = '%Y:%m:%d %H:%M:%S'
            if filename_datetime_has_subsec:
                dtfmt += '.%f'
                tag = Tags.SubSecDateTimeOriginal
            else:
                tag = Tags.DateTimeOriginal

            class _Fix(Fix):
                description = f'Set {tag} from the filename? {filename}. Timestamp will be {filename_datetime}'

                def fix(self) -> None:
                    assert mime == 'image/jpeg', path  # TODO later support more
                    # otherwise can end up in an inconsistent state?
                    dts = filename_datetime.strftime(dtfmt)

                    exiftool_set_tag(path=path, tag=tag, value=dts, comment=f'inferred {tag} from the filename {filename}')
                    # todo might be nice to combine with tz settings, but a bit tricky

            yield (xxerr + f'. Has filename timestamp {filename_datetime}, use --fix to set it', _Fix())
            # ok, if we have coordinates, then we can figure out local time from that
            # and also offset..
            # TODO hmm actually not sure about it
            # seems like GPS generally lags behind datetimeoriginal, sometimes by minutes
            # (perhaps if the phone was out of GPS reach or something...)
            # so perhaps better options is to parse from filename? it's supposed to be local time anyway...
            # it still generally mismatches by a few seconds but perhaps better than nothing..
            #
            # can still use gps as a sanity check? e.g. emit warning if photo is more than 30 min away..
            #
        else:
            yield xxerr


    # let's see if we can figure out the timezone
    def check_tz_offset() -> Generator[
            Error | tuple[Error, Fix],
            None,
            str | None,
    ]:
        dt_offset_s = row.get(Tags.OffsetTimeOriginal)
        if dt_offset_s is not None:
            # TODO check first that it's valid?
            return dt_offset_s

        tz_tags = {
            **{tag: row.get(tag) for tag in TZ_TAGS},
            **{tag: exif.get(tag) for tag in TZ_EXTRA},
        }

        notnone = {k: v for k, v in tz_tags.items() if v is not None}

        prefix = f'missing {Tags.OffsetTimeOriginal}: '

        if len(notnone) > 0:
            tz_err = prefix + f'maybe you can figure it out from {notnone}'
            if Tags.OffsetTime in notnone:
                ot_value = notnone[Tags.OffsetTime]

                class FixOffsetFromOtherTag(Fix):
                    description = f'Set {Tags.OffsetTimeOriginal} from {Tags.OffsetTime}?'

                    def fix(self) -> None:
                        assert mime == 'image/jpeg', path  # just in case

                        exiftool_set_tag(
                            path=path,
                            tag=Tags.OffsetTimeOriginal,
                            value=ot_value,
                            comment=f'inferred {Tags.OffsetTimeOriginal} from {Tags.OffsetTime}',
                        )

                yield tz_err + f', use --fix to set it from {Tags.OffsetTime}', FixOffsetFromOtherTag()
            else:
                yield tz_err  # TODO this is more of a fallback error? return as the last resort?

        if coordinate is None:
            yield prefix + "no GPS coordinates, so can't infer out the offset"
            return None

        if dt_orig_res is None:
            # NOTE: even if we have GPSDateTime here,
            # there isn't much we can do
            # doesn't make sense to set OffsetTimeOriginal without DateTimeOriginal
            yield prefix + "has GPS coordinates, but no local time, so can't infer the offset"
            return None

        # ok, we have a coordinate and local time, so possible to infer and set the timezone

        target = Tags.OffsetTimeOriginal

        class FixOffsetFromGPS(Fix):
            @property
            def description(self) -> str:
                return f'Set {target} to {self.offset_s}?'

            def fix(self) -> None:
                # NOTE: when we do that, exiftool also computes SubSecDateTimeOriginal
                # which has both datetime and tz offset
                # see https://github.com/photoprism/photoprism/issues/2320
                # perhaps later should ensure we have all necessary DateTimeOriginal tags?
                exiftool_set_tag(path=path, tag=target, value=self.offset_s, comment=f'inferred {target} from GPS')

            @cached_property  # cached because computing timezone from coordinates is expensive
            def offset_s(self) -> str:
                # need to convince mypy again
                assert coordinate is not None
                assert dt_orig_res is not None
                # https://github.com/python/mypy/issues/2608#issuecomment-1689653609


                (lat_s, lon_s) = coordinate
                point = geopy.Point.from_string(
                    (lat_s + ' ' + lon_s).replace('deg', '').replace('"', "''").replace(",", '')
                )
                # NOTE ok, using fast=False can slow things down significantly..
                # but it seems that I do have a bit of difference in a few cases...
                tzfinder = timezone_finder(fast=False)
                tzname = tzfinder.timezone_at(lat=point.latitude, lng=point.longitude)
                assert tzname is not None, point
                tz = pytz.timezone(tzname)

                offset = tz.localize(dt_orig_res).utcoffset()
                assert offset is not None
                tots = int(offset.total_seconds())
                sign = '+' if tots >= 0 else '-'
                hh, mm = divmod(abs(tots), 3600)
                offset_s = f'{sign}{hh:02d}:{mm:02d}'
                return offset_s

        yield prefix + f'has GPS coordinates and local datetime {dt_orig_res}. Use --fix to set the offset', FixOffsetFromGPS()
        return None


    # doesn't look like quicktime (which includes mp4) supports tz offsets :(
    # perhaps the reason is that creation time supposed to be UTC, but most cameras store local time
    # see "API QuickTimeUTC" in exiftool docs
    supports_tz_offset = mime not in {'video/mp4', 'video/quicktime', 'video/x-msvideo'}

    if supports_tz_offset:
        tz_offset = yield from check_tz_offset()
    else:
        tz_offset = None



def check_all_media(*, db_path: str, regex: str | None, apply_fixes: bool, dir_summary: bool, config: Config) -> None:
    db = Db(db_path=db_path)

    errs_per_directory: dict[Path, int] = {}

    fixers = []
    # todo report progress?
    for row in db.query():
        path = row.path

        if not config.include_filename(path=path):
            continue

        if regex is not None and not re.search(regex, path):
            continue

        pp = Path(path)
        if not pp.exists():
            logger.debug(f"{pp} doesn't exist anymore.. ignoring")
            continue

        xrow = Row(row=row)

        d = pp.parent
        if d not in errs_per_directory:
            errs_per_directory[d] = 0


        row_fixer = None

        # NOTE: we only collect first 'fix' per row
        # don't want to apply multiple since the results of one fix might depend on previous fixes
        # e.g. after fixing datetimecreated we might be able to set tz offset properly
        for check_res in check_media(row=xrow):
            err: str
            fixer: Fix | None
            if isinstance(check_res, tuple):
                (err, fixer) = check_res
            else:
                err = check_res
                fixer = None

            if config.ignore_error(row=xrow, error=err):
                continue

            if fixer is not None and row_fixer is None:
                row_fixer = (err, fixer)

            errs_per_directory[d] += 1
            logger.error(f'{path:<100} : {err}')

        if row_fixer is not None:
            err, fixer = row_fixer
            fixers.append((path, err, fixer))

    if dir_summary:
        logger.info('summary of errors per directory')
        for d, count in sorted(errs_per_directory.items(), key=lambda p: p[1]):
            if count == 0:
                continue
            logger.info(f'{str(d):<100} : {count}')

    if apply_fixes and len(fixers) > 0:
        logger.info('suggested fixes:')

        # first just print them all
        for path, err, fixer in fixers:
            logger.info(f'{path:<100} : {err}')
            print(f'       suggested fix: {fixer.description}', file=sys.stderr)

        logger.info('offering to fix')

        auto_apply = False
        for path, err, fixer in fixers:
            logger.info(f'{path:<100} : {err}')
            logger.info(f'   suggested fix: {fixer.description}')

            if not auto_apply:
                choices = click.Choice(choices=[
                    'y',  # yes (one item)
                    'n',  # no (one item)
                    'a',  # all remaining
                    'x',  # abort
                ])
                cr = click.prompt(text="   apply fix?", default='y', type=choices, show_choices=True)
                if cr == 'x':
                    # todo error or something?
                    return
                if cr == 'n':
                    continue

                if cr == 'a':
                    auto_apply = True

                assert cr in {'a', 'y'}  # just in case
            fixer.fix()


    # TODO check timestamps for consistency as well?
    # TODO sanity checks for coordinates? not sure which, maybe just check for zeros first

    # TODO exit code?


# TODO remove 'photos' from named and docs -- should be 'media' cause it supports videos too
@click.command()
# todo take from the config?
@click.option('--db', default=f'{ROOT_DIR}/exifs.sqlite', help='path to exif database', show_default=True)
@click.option('--regex', default=None, help='only process media matching this regex')
@click.option('--fix', is_flag=True, help='offer to fix tags')
@click.option('--dir-summary', is_flag=True, help='print summary of errors per directory')
@click.option('--config', default=None, help='configuration file')
def main(db: str, regex: str | None, fix: bool, dir_summary: bool, config: str | None) -> None:
    if config is None:
        cfg = Config()
    else:
        _globs = {}  # type: ignore[var-annotated]
        exec(Path(config).read_text(), _globs)
        cfg = _globs['Config']()

    check_all_media(
        db_path=db,
        regex=regex,
        apply_fixes=fix,
        dir_summary=dir_summary,
        config=cfg,
    )


if __name__ == '__main__':
    main()
