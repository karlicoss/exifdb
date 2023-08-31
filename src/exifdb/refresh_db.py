#!/usr/bin/env python3
import argparse
from datetime import datetime, timezone
from pathlib import Path
from subprocess import check_output, check_call
import sys
from tempfile import TemporaryDirectory
from typing import Iterable, Iterator, IO, Any


import click
from loguru import logger
from more_itertools import chunked
import orjson


def exclude(p: Path) -> bool:
    if p.suffix.lower() in {
        '.png',  # TODO I guess these are screenshots? probs best to remove?
        '.sh',   # might have some scripts inside
        '.webp', # e.g. videos saved from instagram... probably not much we can do with these
        '.thm',
    }:
        return True
    if p.name in {
        '.DS_Store',
        'TODO',
        'NOTES',
        '.nomedia',
        'Thumbs.db',
    }:
        return True
    if '.dtrash' in p.parts:
        return True
    return False


ALLOWED_EXTS = {
    '.jpg', '.jpeg',
    '.mp4',
    '.avi',
    '.mov',
    '.heic', '.heif',  # iphone photos

    # files modified by exiftool
    # TODO should double check and remove if I don't need them
    '.jpg_original',
}


EXIF_DIFF_EXCLUDE = {
    ## these always change during update
    'FileAccessDate',
    'FileModifyDate',
    'FileInodeChangeDate',
    'FileSize',
    ##

    ## not very inereseting (digikam sets it during some updates)
    # during gps updates
    'JFIFVersion',
    'ThumbnailOffset',
    'XMPToolkit',
    #

    # during setting tags
    'CurrentIPTCDigest',
    'CodedCharacterSet',
    'ExifByteOrder',
    'InteropIndex',
    'InteropVersion',
    'EnvelopeRecordVersion',
    'ApplicationRecordVersion',
    #

    'OtherImageStart',  # offset, might change when we change exif
    'MPImageStart',

    # seems like they may appear if the image didn't have any tags before?
    'YCbCrPositioning',
    'ColorSpace',
    'ExifVersion',
    'FlashpixVersion',
    'ComponentsConfiguration',
}


EXIF_DIFF_ALLOW = {
    ## keywords/tags
    'CatalogSets',
    'Categories',
    'HierarchicalSubject',
    'Keywords',
    'XPKeywords',
    'TagsList',
    'LastKeywordXMP',
    'Subject',
    ##

    'Comment',
    'UserComment',
    'Description',
    'Caption-Abstract',
    'Notes',
    'ImageDescription',

    'GPSMapDatum',
    'GPSVersionID',
    'Warning',

    'DateTimeOriginal',
    'OffsetTimeOriginal',
    'SubSecDateTimeOriginal',
    'SubSecTimeOriginal',
    'DateTime',
    'DateCreated',
    'DateTimeCreated',
    'TimeCreated',
    'MetadataDate',
}


def get_photos(root: Path) -> list[Path]:
    paths = [
        p
        for p in root.rglob('*')
        if not p.is_dir() and not p.is_symlink() and not exclude(p)
    ]
    for p in paths:
        assert p.suffix.lower() in ALLOWED_EXTS, p
    return sorted(map(Path, paths))



def iter_exifs(photos: Iterable[Path]) -> Iterator:
    # ok, this is processing in bulk, but still a bit slow to process everything?
    # exiftool -j -r -w '/tmp/exif/%d%f.meta' '/path/to/photos/'
    # 2600 files:
    # - with n=10  in 42s
    # - with n=100 in 24s
    # - with n=300 in 22s
    for group in chunked(photos, n=100):
        results = orjson.loads(check_output(['exiftool', '-j', *group], text=True))
        assert len(results) == len(group)  # just in case
        yield from results


# TODO special mode to only check changes -- still opens the db as read only but doesn't write anything
# TODO need to delete photos we removed from db
def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--db', type=Path, required=True)
    args = p.parse_args()

    root = args.root
    db = Path(args.db)

    from sqlalchemy import create_engine
    import sqlite3
    echo = False
    # ?immutable=1
    engine = create_engine('sqlite://', creator=lambda: sqlite3.connect(f'file:{db}', uri=True), echo=echo)

    from sqlalchemy import MetaData, Table, Column, String, Computed, text
    meta = MetaData()

    exif_cols = [
        'MIMEType',  # kinda convenient for filtering

        # this doesn't contain tz offset
        'DateTimeOriginal',
        # so need to pair with this one
        # note that it's relatively new (2016), so likely older photos won't have it
        'OffsetTimeOriginal',

        # still tho, sometimes there is no offset
        # in which case gps datetime (which is utc) is useful
        # that said not sure whether software actually uses it.
        # e.g. Digikam seems to ignore it even if photos have no other timestamps
        'GPSDateTime',

        # only for heic format? contains timezone
        # todo ^ above comment is probably irrelevant, it's a composite field?
        'SubSecDateTimeOriginal',

        # present on .mov files from iphone?
        # seems like the only useful field with orig date
        'ContentCreateDate',
        # there is also 'CreateDate', but it doesn't contain timezone data?

        # for newer photos (from Pixel) we do seem to have it though -- maybe check could be date dependent..
        # ugh. often missing, even though GPSDateTime is present (also gps usually utc anyway which is good)
        # 'OffsetTimeOriginal'

        # TODO for digikam the order is
        # see core/libs/metadataengine/engine/metaengine_item.cpp
        'DateTimeDigitized',
        'DateCreated',
        'CreateDate',
        # there are a bunch more, but don't want to pollute for now


        'GPSLatitude',
        'GPSLatitudeRef',
        'GPSLongitude',
        'GPSLongitudeRef',
        'GPSAltitude',

        # ok, apparently this one is more for convenience
        # software should generally rely on the tags above (same with GPSCoordinate)
        # Actually GpsCoordinates seems like a quicktime tag, and it's mapped to GpsPosition by exiftool
        'GPSPosition',

        # this is for mp4
        # there are also 'ModifyDate', 'TrackCreateDate', 'TrackModifyDate', 'MediaCreateDate', 'MediaModifyDate',
        # but this suggests CreateDate is the most accurate https://superuser.com/a/1285932/300795
        # TODO is it utc?
        'CreateDate',
        #
        # https://www.photoprism.app/kb/metadata
        # Description text Description, Caption-Abstract
        # Notes       text Comment, UserComment
        # Subject 	text 	Subject, PersonInImage, ObjectName, HierarchicalSubject, CatalogSets
        # Title 	text 	Headline, Title
        # CreatedAt 	timestamp 	SubSecCreateDate, CreationDate, CreateDate, MediaCreateDate, ContentCreateDate, TrackCreateDate
        # TakenAt 	timestamp 	SubSecDateTimeOriginal, SubSecDateTimeCreated, DateTimeOriginal, CreationDate, DateTimeCreated, DateTime, DateTimeDigitized 	DateCreated
        # TakenAtLocal 	timestamp 	SubSecDateTimeOriginal, SubSecDateTimeCreated, DateTimeOriginal, CreationDate, DateTimeCreated, DateTime, DateTimeDigitized
        # TakenGps 	timestamp 	GPSDateTime, GPSDateStamp
        #
        # NOTE: digikam always recommends XMP tags, they have better features?
        # digikam settings in "advanced" contain mapping to exif/xmp tags
        # uhh. so during setting tags, digikam sets
        # Categories
        # TagsList
        # LastKeywordXMP
        # HierarchicalSubject -- supported by photoprism
        # CatalogSets         -- supported by photoprism
        # Subject             -- supported by photoprism
        'Keywords',  #        -- supported by photoprism
        # all of these seem to be lists (some xml, some yaml)
    ]


    extras: dict[str, Any] = {}
    if db.exists():
        # if it already exists we wanna load column names from sqlite
        extras['autoload_with'] = engine
        # otherwise we can't pass autoload_with
    data = Table(
        'data',
        meta,
        Column('path', String, primary_key=True),
        Column('mtime', String),
        Column('exif', String),

        # derived cols
        # *(Column(c, String, Computed(f"json_extract(exif, '$.{c}')", persisted=False)) for c in exif_cols),
        **extras,
    )
    meta.create_all(engine)

    ## create virtual cols if they don't exist..
    ## sadly can't do it via sqlalchemy, it doesn't support migrations at all?
    existing_cols = {c.name for c in data.columns}
    for c in exif_cols:
        if c in existing_cols:
            continue
        logger.info(f'creating virtual column {c}')
        # ugh, not sure how to compile this via sqlalchemy
        # column = Column(c, String, Computed(f"json_extract(exif, '$.{c}')", persisted=False))
        column_type = f"VARCHAR GENERATED ALWAYS AS (json_extract(exif, '$.{c}')) VIRTUAL"
        with engine.connect() as conn:
            conn.execute(text(f'ALTER TABLE {data.name} ADD COLUMN {c} {column_type}'))
    ##


    existing: dict[str, str] = {}
    from sqlalchemy import select
    with engine.connect() as conn:
        for path, mtime in conn.execute(select(data.c.path, data.c.mtime)):
            existing[path] = mtime

    def uptodate(p: Path, mtime: datetime) -> bool:
        ps = str(p)
        em = existing.get(ps)
        if em is None:
            return False
        return mtime.isoformat() == em

    photos = get_photos(root)
    logger.info(f'discovered {len(photos)} photo in {root}')
    photos_with_mtime = [
        (p, datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).replace(microsecond=0))
        for p in photos
    ]
    photos_with_mtime = [(p, m) for (p, m) in photos_with_mtime if not uptodate(p, m)]

    if len(photos_with_mtime) == 0:
        logger.info('no changes! exiting')
        engine.dispose()
        sys.exit(0)

    inserts = []
    updates = []
    for ((photo, mtime), exif) in zip(photos_with_mtime, iter_exifs(p for p, _ in photos_with_mtime)):
        # TODO not sure if we need any special options for orjson?
        entry = (str(photo), mtime.isoformat(), orjson.dumps(exif))
        if str(photo) in existing:
            updates.append(entry)
        else:
            inserts.append(entry)

    logger.info(f'{len(inserts)} inserts, {len(updates)} updates!')

    if len(updates) > 0:
        befores = []
        afters = []
        with engine.begin() as conn:
            # todo make more atomic?
            for path, _, new_exif_s in updates:
                # todo not sure if need mtimes here?
                [(old_exif_s,)] = conn.execute(select(data.c.exif).where(data.c.path == path))

                old_exif = orjson.loads(old_exif_s)
                new_exif = orjson.loads(new_exif_s)
                for k in {*old_exif.keys(), *new_exif.keys()}:
                    if k in EXIF_DIFF_EXCLUDE:
                        continue

                    # setting to none makes vimdiff a bit nicer
                    if k not in old_exif:
                        old_exif[k] = None
                    if k not in new_exif:
                        new_exif[k] = None

                    ov = old_exif.get(k)
                    nv = new_exif.get(k)
                    if ov == nv:
                        old_exif.pop(k, None)
                        new_exif.pop(k, None)
                    else:
                        assert k in EXIF_DIFF_ALLOW, (path, k, old_exif, new_exif)

                old_exif_s = orjson.dumps(old_exif)
                new_exif_s = orjson.dumps(new_exif)

                befores.append((path, old_exif_s))
                afters.append((path, new_exif_s))

        def dump(datas, fo: IO[bytes]) -> None:
            for path, exif in datas:
                exj = orjson.loads(exif)
                for k in EXIF_DIFF_EXCLUDE:
                    exj.pop(k, None)

                for line in orjson.dumps(
                        exj,
                        option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS,
                ).splitlines():
                    fo.write(path.encode('utf8') + b' ' + line + b'\n')

        with TemporaryDirectory() as td:
            tdir = Path(td)
            before = tdir / 'before'
            after = tdir / 'after'

            with before.open('wb') as fo:
                dump(befores, fo)
            with after.open('wb') as fo:
                dump(afters, fo)

            # TODO maybe instead of vimdiff properly compute differences?

            check_call([
                'vimdiff',
                # denser diff
                '-c', 'set diffopt=filler,context:0',
                before,
                after,
            ])

    click.confirm('happy to proceed?', abort=True)

    if len(inserts) > 0:
        with engine.begin() as conn:
            conn.execute(data.insert().values(inserts))

    if len(updates) > 0:
        with engine.begin() as conn:
            # a bit shit, but seems like sqlite doesn't support bulk updates?
            # might be easier to just do insert and overwrite on conflict..
            for path, mtime, exif in updates:
                conn.execute(data.update().values(mtime=mtime, exif=exif).where(data.c.path == path))

    engine.dispose()
    # TODO wal mode? take from promnesia


if __name__ == '__main__':
    main()
