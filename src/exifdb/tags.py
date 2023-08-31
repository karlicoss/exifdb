class Tags:
    '''
    Non-exhaustive list of exif tags that are often referred in the project
    '''

    # fmt: off
    # Digikam prefers this as the datetime source
    # see core/libs/metadataengine/engine/metaengine_item.cpp
    # generally seems that it's the most reliable one
    # it doesn't contain TZ though?
    DateTimeOriginal       = 'DateTimeOriginal'
    DateTimeDigitized      = 'DateTimeDigitized'
    # ok, some of my photos from NY only have this one
    CreateDate             = 'CreateDate'

    OffsetTimeOriginal     = 'OffsetTimeOriginal'
    GPSDateTime            = 'GPSDateTime'
    SubSecDateTimeOriginal = 'SubSecDateTimeOriginal'
    ContentCreateDate      = 'ContentCreateDate'

    GPSLatitude            = 'GPSLatitude'
    GPSLatitudeRef         = 'GPSLatitudeRef'
    GPSLongitude           = 'GPSLongitude'
    GPSLongitudeRef        = 'GPSLongitudeRef'
    # fmt: on


DT_TAGS = [
    Tags.DateTimeOriginal,
    Tags.DateTimeDigitized,
    Tags.CreateDate,

    Tags.SubSecDateTimeOriginal,
    Tags.ContentCreateDate,

    Tags.GPSDateTime,
]

TZ_TAGS = [
    Tags.OffsetTimeOriginal,
]

# also see https://exiftool.org/TagNames/GPS.html
GPS_TAGS = [
    Tags.GPSLatitude,
    Tags.GPSLongitude,

    # hmm in theory the exif standard says we need to rely on the ref (i.e. N/S/W/E)
    # but in practice lat/lon already contain the ref
    # also mp4 videos don't have the ref tag at all
    # e.g. seems like  Digikam infers the references from GPSLatitude/GPSLongitude
    # I think might have something to do with exif vs xmp tags?
    # Tags.GPSLatitudeRef,
    # Tags.GPSLongitudeRef,
]
# TODO right, this explains the above
# basicaly, there is a composite GPSLatitude tag
# and then there is an original EXIF GPSLatitude, which doesn't have the reference thing
# need to think which ones I wanna use here
# exiftool -a -G testphoto.jpg  -j
# also see https://exiftool.org/TagNames/Composite.html


# datetime-like tags that might be potentially useful
DT_EXTRA = {
    # istra teambuilding? TODO private
    'TimeStamp',

    # on photo sphere?
    'FirstPhotoDate', 'LastPhotoDate',

    # someone elses pic from houswarming?
    'DigitalCreationDate', 'DigitalCreationTime', 'DigitalCreationDateTime',

    'ModifyDate',
    'SubSecCreateDate',
    'SubSecModifyDate',
    'SubSecModifyDate',
    'TimeCreated',
    'MetadataDate',
    'DateCreated',
    'HistoryWhen',
    'DateTimeCreated',
    'DateTime',
    'Date',

    # present on mp4
    'TrackCreateDate',
    'TrackModifyDate',
    'MediaCreateDate',
    'MediaModifyDate',

    # present on mov files
    'CreationDate',
}
# todo make sure doesn't overlap with DT_TAGS?


# tz-like tags that might be potentially useful
TZ_EXTRA = {
    'OffsetTime',
    'OffsetTimeDigitized',

    # hm, this was only present on one photo (not even mine)
    # seems like this MakerNotes:TimeZone
    'TimeZone',
}


# todo document properly
TAGS_WITH_COLON = {
    ## synthetic tags, not actually exif
    'FileModifyDate',
    'FileAccessDate',
    'FileInodeChangeDate',
    ##

    ## mark already known tags
    *DT_EXTRA,
    *DT_TAGS,
    *TZ_EXTRA,
    *TZ_TAGS,
    ##

    ## these are only parts of the composite GPSTimeStamp tag
    'GPSDateStamp',
    'GPSTimeStamp',
    ##

    # colour profile or something, has nothing to do with photo
    'ProfileDateTime',

    ## just happen to contain ":"
    'YCbCrSubSampling',
    'SpecialMode',
    'About',
    'FocalLength35efl',
    'DerivedFromInstanceID',
    'DerivedFromDocumentID',
    'DeviceMfgDesc',
    'DocumentID',
    'XMPToolkit',
    'Warning',
    'RunTimeSincePowerUp',
    'UserComment',
    'ImageDescription',
    'Comment',
    'Caption-Abstract',
    'Prefs',
    'Profiles',
    'Cameras',
    'ContainerDirectory',
    'HistoryInstanceID',
    'InstanceID',
    'Artist',
    'Copyright',
    'AspectRatio',
    'Lens35efl',
    'CanonImageType',
    'PowerUpTime',
    'HandlerDescription',
    'MajorBrand',
    'MediaDuration',
    'TrackDuration',
    'Duration',
    'PixelAspectRatio',
    'ChromaFormat',
    'AuxiliaryImageType',
}


# misc notes
# huh, seems like for video digikam does something hacky and retrofits Quicktime tags into XMP and EXIF?
# core/libs/metadataengine/dmetadata/dmetadata_video.cpp
# also see https://exiftool.org/TagNames/QuickTime.html
