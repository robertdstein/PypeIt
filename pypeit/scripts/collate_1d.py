"""
This script collates multiple 1d spectra in multiple files by object, 
runs flux calibration/coadding on them, and produces files suitable
for KOA archiving.

.. include common links, assuming primary doc root is up one directory
.. include:: ../include/links.rst
"""

from datetime import datetime
from glob import glob
import os.path
from functools import partial
import re
import traceback

import numpy as np
from astropy.coordinates import Angle
from astropy.io import fits
from astropy.time import Time
from pypeit.par import pypeitpar
from pypeit.spectrographs.util import load_spectrograph
from pypeit import coadd1d
from pypeit import msgs
from pypeit import par
from pypeit.utils import is_float
from pypeit.archive import ArchiveMetadata, ArchiveDir
from pypeit.core.collate import collate_spectra_by_source, SourceObject
from pypeit.scripts import scriptbase
from pypeit.slittrace import SlitTraceBitMask
from pypeit.spec2dobj import AllSpec2DObj


def extract_id(header):
    """
    Pull an id from a file's header.

    This will give preference to a KOAID, but will return an id based on the
    file name if a KOAID can't be found.  A KOAID is of the format
    II.YYYYMMDD.xxxxx.fits. See the `KOA FAQ
    <https://www2.keck.hawaii.edu/koa/public/faq/koa_faq.php>`_ for more
    information.

    Args:
        header (str):   A fits file header.

    Returns:
        str: The an id extracted from the header.
    """

    # First check for the KOAID keyword

    if 'KOAID' in header:
        return header['KOAID']
    else:
        # Attempt to pull KOAID from file name
        filename = header['FILENAME']
        if len(filename) >= 17:
            koaid = filename[0:17]
            if re.match(r'..\.\d{8}\.\d{5}$', koaid) is not None:
                # KOA seems to append .fits to the ID
                return koaid + ".fits"

        # For non KOA products, we use the filename
        return filename

def get_metadata_by_id(header_keys, file_info):
    """
    Gets the metadata from a FITS header used for the by id portion
    of the archive. It is intended to be wrapped in by functools
    partial object that passes in header_keys. file_info
    is then passed as in by the :obj:`pypeit.archive.ArchiveMetadata` object.

    If another type of file is added to the ArchiveMetadata object, the file_info
    argument will not be a string, In this case, a list of ``None`` values are
    returned.

    Args:
        header_keys (list of str):
            List of FITs header keywords to read from the file being added to the
            archive.

        filename (str): A filename for a file to add to the ArchiveMetadata object.
    
    Returns:
        tuple: data_rows, files_to_copy

        data_rows (list of list):
            The metadata rows built from the FITS file.

        files_to_copy (iterable):
            An iterable of tuples. Each tuple has a src file to copy to the archive
            and a relative pathname for that file in the archive. The file will be copied
            to the dest pathname relative to the archive's root.

        
    """
    # Source objects are handled by get_object_based_metadata
    if isinstance(file_info, SourceObject):
        return (None, None)

    # Anything else should be a tuple of filename, txt info, pypeit file

    # Place the files in a subdir of the archive based on the observation date
    # This is intended to prevent any one directory from having too many files

    filename = file_info[0]
    header = fits.getheader(filename)
    subdir_name = Time(header['MJD'], format='mjd').strftime("%Y%m")
    dest_files = [os.path.join(subdir_name, os.path.basename(x)) for x in file_info]

    # Extract koa id from source image filename in header
    id = extract_id(header)

    # Build data row, which starts with koaid, filenames within the archvie, + the metadata
    data_row = [id] + dest_files + [None if x not in header else header[x] for x in header_keys]

    return ([data_row], zip(file_info, dest_files))

def get_object_based_metadata(object_header_keys, spec_obj_keys, file_info):
    """
    Gets the metadata from a SourceObject instance used for the by object
    portion of the archive. It is intended to be wrapped in by functools
    partial object that passes in object_header_keys and spec_obj_keys. file_info
    is then passed as in by the :obj:`pypeit.archive.ArchiveMetadata` object.

    If another type of file is added to the ArchiveMetadata object, the file_info
    argument will not be a SourceObject, In this case, a list of ``None`` values are 
    returned.

    Args:
        object_header_keys (list of str):
            The keys to read fom the spec1d headers from the SourceObject.

        spec_obj_keys (list of str):
            The keys to read from the (:obj:`pypeit.specobj.SpecObj`) objects in the SourceObject.

        file_info (:obj:`pypeit.scripts.collate_1d.SourceObject`)): 
            The source object containing the headers, filenames and SpecObj information for a coadd output file.

    Returns:
        list of list:
            The list of metadata rows built from the source object.

        files_to_copy (list):
            An list of tuples. Each tuple has a src file to copy to the archive
            and a relative pathname for that file in the archive. The file will be copied
            to the dest pathname relative to the archive's root.
    """

    if not isinstance(file_info, SourceObject):
        return (None, None)

    # Place the file in a subdir of the archive based on the observation date
    # This is intended to prevent any one directory from having too many files
    header = fits.getheader(file_info.coaddfile)
    subdir_name = Time(header['MJD'], format='mjd').strftime("%Y%m")
    coaddfile = os.path.join(subdir_name, os.path.basename(file_info.coaddfile))

    result_rows = []
    for i in range(len(file_info.spec1d_header_list)):

        # Get the spec_obj metadata needed for the archive
        spec_obj = file_info.spec_obj_list[i]
        # Use getattr for the spec_obj data because one of the attributes is actually a property (med_s2n)
        spec_obj_data = [getattr(spec_obj, x) for x in spec_obj_keys]

        # Get the spec1d header metadata needed for the archive
        # Use the MJD in the spec1d file to build it's subdirectory, just like get_metadata_by_id does
        # when the spec1d is added to the archive
        header = file_info.spec1d_header_list[i]

        # Get the KOAID of the original image for the spec1d
        id = extract_id(header)


        subdir_name = Time(header['MJD'], format='mjd').strftime("%Y%m")
        spec1d_filename = os.path.join(subdir_name, os.path.basename(file_info.spec1d_file_list[i]))

        header_data = [header[x] if x in header else None for x in object_header_keys]
        result_rows.append([coaddfile] + spec_obj_data + [id, spec1d_filename] + header_data)

    return (result_rows, [(file_info.coaddfile, coaddfile)])

def get_report_metadata(object_header_keys, spec_obj_keys, file_info):
    """
    Gets the metadata from a SourceObject instance used building a report
    on the results of collation. Unlike the other get_*_metadata functions, this
    is not used for archiving.  It is intended to be wrapped in by functools
    partial object that passes in object_header_keys and spec_obj_keys. file_info
    is then passed as in by the :obj:`pypeit.archive.ArchiveMetadata` object.

    If another type of file is added to the ArchiveMetadata object, the file_info
    argument will not be a SourceObject, In this case, a list of ``None`` values are 
    returned.

    Args:
        object_header_keys (list of str):
            The keys to read fom the spec1d headers from the SourceObject.

        spec_obj_keys (list of str):
            The keys to read from the (:obj:`pypeit.specobj.SpecObj`) objects in the SourceObject.

        file_info (:obj:`pypeit.scripts.collate_1d.SourceObject`)): 
            The source object containing the headers, filenames and SpecObj information for a coadd output file.

    Returns:
        list of list:
            The list of metadata rows built from the source object.

        files_to_copy (list):
            An list of tuples of files to copy. Because this function is not used for
            archving data, this is always None.
    """

    if not isinstance(file_info, SourceObject):
        return (None, None)

    coaddfile = os.path.basename(file_info.coaddfile)
    result_rows = []
    for i in range(len(file_info.spec1d_header_list)):

        # Get the spec_obj metadata needed for the report
        spec_obj = file_info.spec_obj_list[i]
        header = file_info.spec1d_header_list[i]

        # Get the spec1d header metadata needed for the report
        # Use getattr for the spec_obj data because one of the attributes is actually a property (med_s2n)
        spec_obj_data = [getattr(spec_obj, x) for x in spec_obj_keys]
        spec1d_filename =  os.path.basename(file_info.spec1d_file_list[i])
        header_data = [header[x] if x in header else None for x in object_header_keys]
        result_rows.append([coaddfile] + spec_obj_data + [spec1d_filename] + header_data)

    return (result_rows, None)


def find_slits_to_exclude(spec2d_files, par):
    """
    Find slits that should be excluded according to the input parameters.

    The slit mask ids are returned in a map alongside the text labels for the
    flags that caused the slit to be excluded.

    Args:
        spec2d_files (:obj:`list`): 
            List of spec2d files to build the map from.
        par (:class:`~pypeit.par.pypeitpar.Collate1DPar`):
            Parameters from a ``.collate1d`` file

    Returns:
        :obj:`dict`: Mapping of slit mask ids to the flags that caused the slit
        to be excluded.
    """

    # Get the types of slits to exclude from our parameters
    exclude_flags = par['collate1d']['exclude_slit_trace_bm']
    if isinstance(exclude_flags, str):
        exclude_flags = [exclude_flags]

    # Go through the slit_info of all spec2d files and find
    # which slits should be excluded based on their flags
    bit_mask = SlitTraceBitMask()
    exclude_map = dict()
    for spec2d_file in spec2d_files:

        allspec2d = AllSpec2DObj.from_fits(spec2d_file)
        for sobj2d in [allspec2d[det] for det in allspec2d.detectors]:
            for (slit_id, mask, slit_mask_id) in sobj2d['slits'].slit_info:
                for flag in exclude_flags:
                    if bit_mask.flagged(mask, flag):
                        if slit_mask_id not in exclude_map:
                            exclude_map[slit_mask_id] = {flag}
                        else:
                            exclude_map[slit_mask_id].add(flag)

    return exclude_map

def exclude_source_objects(source_objects, exclude_map, par):
    """
    Exclude :class:`~pypeit.core.collate.SourceObject` objects based on a slit
    exclude map and the user's parameters.

    Args:
        source_objects (:obj:`list`): 
            List of uncollated :class:`~pypeit.core.collate.SourceObject`
            objects to filter. There should only be one
            :class:`~pypeit.specobj.SpecObj` per
            :class:`~pypeit.core.collate.SourceObject`.
        exclude_map (:obj:`dict`): 
            Mapping of excluded slit ids to the reasons they should be excluded.
        par (:class:`~pypeit.par.pypeitpar.PypeItPar`): 
            Configuration parameters from the command line or a configuration
            file.

    Returns:
        :obj:`list`: A list of :class:`~pypeit.core.collate.SourceObject`
        objects with any excluded ones removed.
    """
    filtered_objects = []
    for source_object in source_objects:

        sobj = source_object.spec_obj_list[0]
        spec1d_file = source_object.spec1d_file_list[0]

        if par['collate1d']['exclude_serendip'] and sobj.MASKDEF_OBJNAME == 'SERENDIP':
            msgs.info(f'Excluding SERENDIP object from {sobj.NAME} in {spec1d_file}')
            continue

        if sobj.MASKDEF_ID in exclude_map:
            msgs.info(f'Excluding {sobj.MASKDEF_ID} in {spec1d_file} because of flags {exclude_map[sobj.MASKDEF_ID]}')
            continue

        if sobj.OPT_COUNTS is None and sobj.BOX_COUNTS is None:
            msgs.warn(f'Excluding {sobj.NAME} in {spec1d_file} because of missing both OPT_COUNTS and BOX_COUNTS')
            continue

        if par['coadd1d']['ex_value'] == 'OPT' and sobj.OPT_COUNTS is None:
            msgs.warn(f'Excluding {sobj.NAME} in {spec1d_file} because of missing OPT_COUNTS. Consider changing ex_value to "BOX".')
            continue

        if par['coadd1d']['ex_value'] == 'BOX' and sobj.BOX_COUNTS is None:
            msgs.warn(f'Excluding {sobj.NAME} in {spec1d_file} because of missing BOX_COUNTS. Consider changing ex_value to "OPT".')
            continue

        filtered_objects.append(source_object)
    return filtered_objects


def coadd(par, source):
    """coadd the spectra for a given source.

    Args:
        par (`obj`:Collate1DPar): Paramters for the coadding
        source (`obj`:SourceObject): The SourceObject with information on
            which files and spectra to coadd.
    """
    par['coadd1d']['coaddfile'] = source.coaddfile
    par['coadd1d']['flux_value'] = False
    spectrograph = load_spectrograph(par['rdx']['spectrograph'])

    # Instantiate
    coAdd1d = coadd1d.CoAdd1D.get_instance(source.spec1d_file_list,
                                           [x.NAME for x in source.spec_obj_list],
                                           spectrograph=spectrograph, par=par['coadd1d'])

    # Run
    coAdd1d.run()
    # Save to file
    coAdd1d.save(source.coaddfile)

def find_spec2d_from_spec1d(spec1d_files):
    """
    Find the spec2d files corresponding to the given list of spec1d files.
    This looks for the spec2d files in  the same directory as the spec1d files.
    It will exit with an error if a spec2d file cannot be found.

    Args:
    spec1d_files (list of str): List of spec1d files generated by PypeIt.

    Returns:
    list of str: List of the matching spec2d files.
    """

    spec2d_files = []
    for spec1d_file in spec1d_files:
        # Check for a corresponding 2d file
        (path, filename) = os.path.split(spec1d_file)
        spec2d_file = os.path.join(path, filename.replace('spec1d', 'spec2d', 1))

        if not os.path.exists(spec2d_file):
            msgs.error(f'Could not find matching spec2d file for {spec1d_file}')

        spec2d_files.append(spec2d_file)

    return spec2d_files

def find_archvie_files_from_spec1d(spec1d_files):
    """
    Find files related to a spec1d file that should be copied to the archive. 
    Currently these are the spec1d text and the .pypeit file. This function
    assumes a directory structure where the .pypeit file is in the parent directory
    of the spec1d file, and the text file is in the same directory as the spec1d file.    
    It will exit with an error if a file cannot be found.

    Args:
    spec1d_files (list of str): List of spec1d files generated by PypeIt.

    Returns:
    tuple: spec1d_text_files, pypeit_files

    spec1d_text_files list of str: List of the matching spec1d text files.
    pypeit_files list of str: List of the matching pypeit files.
    """

    spec1d_text_files = []
    pypeit_files = []
    for spec1d_file in spec1d_files:
        # Check for a corresponding .txt file
        (filepath, ext) = os.path.splitext(spec1d_file)
        text_file = filepath + ".txt"

        if not os.path.exists(text_file):
            msgs.error(f'Could not archive matching text file for {spec1d_file}, file not found.')

        # Check for a corresponding .pypeit file
        found_pypeit_files = glob(os.path.join(os.path.dirname(os.path.dirname(spec1d_file)), '*.pypeit'))

        if len(found_pypeit_files) == 0:
            msgs.error(f'Could not archive matching .pypeit file for {spec1d_file}, file not found.')
        elif len(found_pypeit_files) > 1:
            msgs.error(f'Could not archive matching .pypeit file for {spec1d_file}, found more than one file.')
        
        spec1d_text_files.append(text_file)
        pypeit_files += found_pypeit_files

    return spec1d_text_files, pypeit_files

def build_parameters(args):
    """
    Read the command-line arguments and the input ``.collate1d`` file (if any), 
    to build the parameters needed by ``collate_1d``.

    Args:
        args (`argparse.Namespace`_):
            The parsed command line as returned by the ``argparse`` module.

    Returns:
        :obj:`tuple`: Returns three objects: a
        :class:`~pypeit.par.pypeitpar.PypeItPar` instance with the parameters
        for collate_1d, a
        :class:`~pypeit.spectrographs.spectrograph.Spectrograph` instance with
        the spectrograph parameters used to take the data, and a :obj:`list`
        with the spec1d files read from the command line or ``.collate1d`` file.
    """
    # First we need to get the list of spec1d files
    if args.input_file is not None:
        (cfg_lines, spec1d_files) = par.util.parse_tool_config(args.input_file, 'spec1d', check_files=True)

        # Look for a coadd1d file
        (input_file_root, input_file_ext) = os.path.splitext(args.input_file)
        coadd1d_config_name = input_file_root + ".coadd1d"
        if os.path.exists(coadd1d_config_name):
            cfg_lines += par.util.parse_tool_config(coadd1d_config_name, 'coadd1d')[0]

    else:
        cfg_lines = None
        spec1d_files = []

    if args.spec1d_files is not None and len(args.spec1d_files) > 0:
        spec1d_files = args.spec1d_files

    if spec1d_files is None or len(spec1d_files) == 0:
        msgs.error("A list of spec1d files must be specified via command line or config file.")

    # Get the spectrograph for these files and then create a ParSet. 
    spectrograph = load_spectrograph(spec1d_files[0])
    spectrograph_def_par = spectrograph.default_pypeit_par()

    if cfg_lines is not None:
        # Build using config file
        params = pypeitpar.PypeItPar.from_cfg_lines(cfg_lines=spectrograph_def_par.to_config(), merge_with=cfg_lines)
    else:
        # No config file, use the defaults and supplement with command line args
        params = spectrograph_def_par
        params['collate1d'] = pypeitpar.Collate1DPar()

    # command line arguments take precedence over config file parameters
    if args.tolerance is not None:
        params['collate1d']['tolerance'] = args.tolerance

    if args.match is not None:
        params['collate1d']['match_using'] = args.match

    if args.exclude_slit_bm is not None and len(args.exclude_slit_bm) > 0:
        params['collate1d']['exclude_slit_trace_bm'] = args.exclude_slit_bm

    if args.exclude_serendip:
        params['collate1d']['exclude_serendip'] = True

    if args.dry_run:
        params['collate1d']['dry_run'] = True

    if args.archive_dir is not None:
        params['collate1d']['archive_root'] = args.archive_dir

    return params, spectrograph, spec1d_files

def create_archive(archive_root, copy_to_archive):
    """
    Create an archive with the desired metadata information.

    Metadata is written to three files in the `ipac
    <https://irsa.ipac.caltech.edu/applications/DDGEN/Doc/ipac_tbl.html>`_
    format:

        - ``by_id_meta.dat`` contains metadata for the spec1d and spec2d files
          in the archive. It is organzied by the id (either KOAID, or file name)
          of the original science image. This file is only written if copy_to_archive
          is true.

        - ``by_object_meta.dat`` contains metadata for the coadded output files.
          This may have multiple rows for each file depending on how many
          science images were coadded. The primary key is a combined key of the
          source object name, filename, and koaid columns. This file is only written 
          if copy_to_archive is true.

        - ``report.dat`` contains metadata to report on the coadded output files
          from the collate process. Like ``by_object_meta.dat`` it may have more
          than one row per output file.  This file is always written to the current directory.     

    Args:
        archive_root (:obj:`str`):
            The path to archive the metadata and files
        copy_to_archive (:obj:`bool`):
            If true, files will be stored in the archive.  If false, only
            metadata is stored.

    Returns:
        :class:`~pypeit.archive.ArchiveDir`: Object for archiving files and/or
        metadata.
    """

    archive_metadata_list = []

    ID_BASED_HEADER_KEYS  = ['RA', 'DEC', 'TARGET', 'PROGPI', 'SEMESTER', 'PROGID', 'DISPNAME', 'DECKER', 'BINNING', 'MJD', 'AIRMASS', 'EXPTIME']
    OBJECT_BASED_HEADER_KEYS = ['DISPNAME', 'DECKER', 'BINNING', 'MJD', 'AIRMASS', 'EXPTIME','GUIDFWHM', 'PROGPI', 'SEMESTER', 'PROGID']
    OBJECT_BASED_SPEC_KEYS   = ['MASKDEF_OBJNAME', 'MASKDEF_ID', 'DET', 'RA', 'DEC','med_s2n', 'WAVE_RMS']


    if copy_to_archive:
        by_id_names = ['id', 'filename', 'text_info', 'pypeit_file'] + [x.lower() for x in ID_BASED_HEADER_KEYS]
        by_id_metadata = ArchiveMetadata(os.path.join(archive_root, "by_id_meta.dat"),
                                         by_id_names,
                                         partial(get_metadata_by_id,
                                                 ID_BASED_HEADER_KEYS),
                                         append=True)
        archive_metadata_list.append(by_id_metadata)

        by_object_names = ['filename'] + \
                            [x.lower() for x in OBJECT_BASED_SPEC_KEYS] + \
                            ['source_id', 'spec1d_filename'] + \
                            [x.lower() for x in OBJECT_BASED_HEADER_KEYS]

        by_object_metadata = ArchiveMetadata(os.path.join(archive_root, "by_object_meta.dat"),
                                             by_object_names,
                                             partial(get_object_based_metadata,
                                                     OBJECT_BASED_HEADER_KEYS,
                                                     OBJECT_BASED_SPEC_KEYS),
                                             append=True)
        archive_metadata_list.append(by_object_metadata)

    report_names = ['filename'] + \
                        [x.lower() for x in OBJECT_BASED_SPEC_KEYS] + \
                        ['spec1d_filename'] + \
                        [x.lower() for x in OBJECT_BASED_HEADER_KEYS]

    report_metadata = ArchiveMetadata("collate_report.dat",
                                      report_names,
                                      partial(get_report_metadata,
                                              OBJECT_BASED_HEADER_KEYS,
                                              OBJECT_BASED_SPEC_KEYS),
                                      append=True)
    archive_metadata_list.append(report_metadata)

    # metadatas in archive object
    return ArchiveDir(archive_root, archive_metadata_list, copy_to_archive=copy_to_archive)


class Collate1D(scriptbase.ScriptBase):

    @classmethod
    def get_parser(cls, width=None):
        # A blank Colate1DPar to avoid duplicating the help text.
        blank_par = pypeitpar.Collate1DPar()

        parser = super().get_parser(description='Flux/Coadd multiple 1d spectra from multiple '
                                                'nights and prepare a directory for the KOA.',
                                    width=width, formatter=scriptbase.SmartFormatter)

        # TODO: Is the file optional?  If so, shouldn't the first argument start
        # with '--'?
        parser.add_argument('input_file', type=str,
                            help='R|(Optional) File for guiding the collate process.  '
                                 'Parameters in this file are overidden by the command line. The '
                                 'file must have the following format:\n'
                                 '\n'
                                 'F|[collate1d]\n'
                                 'F|  tolerance             <tolerance>\n'
                                 'F|  archive_root          <directory for archive files>\n'
                                 'F|  exclude_slit_trace_bm <slit types to exclude>\n'
                                 'F|  exclude_serendip      If set serendipitous objects are skipped.\n'  
                                 'F|  match_using           Whether to match using "pixel" or\n'
                                 'F|                        "ra/dec"\n'
                                 'F|  dry_run               If set the matches are displayed\n'
                                 'F|                        without any processing\n'
                                 '\n'
                                 'F|spec1d read\n'
                                 'F|<path to spec1d files, wildcards allowed>\n'
                                 'F|...\n'
                                 'F|end\n',                        
                            nargs='?')
        parser.add_argument('--spec1d_files', type=str, nargs='*',
                            help='One or more spec1d files to flux/coadd/archive. '
                                 'Can contain wildcards')
        parser.add_argument('--par_outfile', default='collate1d.par', type=str,
                            help='Output to save the parameters')
        parser.add_argument('--tolerance', type=str, help=blank_par.descr['tolerance'])
        parser.add_argument('--match', type=str, choices=blank_par.options['match_using'],
                            help=blank_par.descr['match_using'])
        parser.add_argument('--dry_run', action='store_true', help=blank_par.descr['dry_run'])
        parser.add_argument('--archive_dir', type=str, help=blank_par.descr['archive_root'])
        parser.add_argument('--exclude_slit_bm', type=str, nargs='*',
                            help=blank_par.descr['exclude_slit_trace_bm'])
        parser.add_argument('--exclude_serendip', action='store_true',
                            help=blank_par.descr['exclude_serendip'])
        return parser

    @staticmethod
    def main(args):

        start_time = datetime.now()
        (par, spectrograph, spec1d_files) = build_parameters(args)

        # Write the par to disk
        print("Writing the parameters to {}".format(args.par_outfile))
        par.to_config(args.par_outfile)

        # Make sure archive dir, if specified, exists
        if par['collate1d']['archive_root'] is not None:
            os.makedirs(par['collate1d']['archive_root'], exist_ok=True)

        # Parse the tolerance based on the match type
        if par['collate1d']['match_using'] == 'pixel':
            tolerance = float(par['collate1d']['tolerance'])
        else:
            # For ra/dec matching, the default unit is arcseconds. We check for
            # this case by seeing if the passed in tolerance is a floating point number
            if is_float(par['collate1d']['tolerance']):
                tolerance =  float(par['collate1d']['tolerance'])
            else:
                tolerance = Angle(par['collate1d']['tolerance']).arcsec


        # Filter out unwanted source objects based on our parameters.
        # First filter them out based on the exclude_slit_trace_bm parameter
        if len(par['collate1d']['exclude_slit_trace_bm']) > 0:
            spec2d_files = find_spec2d_from_spec1d(spec1d_files)
            exclude_map = find_slits_to_exclude(spec2d_files, par)
        else:
            spec2d_files = []
            exclude_map = dict()

        source_objects = SourceObject.build_source_objects(spec1d_files,
                                                           par['collate1d']['match_using'])

        # Filter based the coadding ex_value, and the exclude_serendip 
        # boolean
        objects_to_coadd = exclude_source_objects(source_objects, exclude_map, par)

        # Collate the spectra
        source_list = collate_spectra_by_source(objects_to_coadd, tolerance)

        #sensfunc, how to identify standard file

        # fluxing etc goes here

        # Coadd the spectra
        successful_source_list = []
        for source in source_list:

            msgs.info(f'Creating {source.coaddfile} from the following sources:')
            for i in range(len(source.spec_obj_list)):
                msgs.info(f'    {source.spec1d_file_list[i]}: {source.spec_obj_list[i].NAME} '
                          f'({source.spec_obj_list[i].MASKDEF_OBJNAME})')

            if not args.dry_run:
                try:
                    coadd(par, source)
                    successful_source_list.append(source)
                except Exception:
                    formatted_exception = traceback.format_exc()
                    msgs.warn(formatted_exception)
                    msgs.warn(f"Failed to coadd {source.coaddfile}, skipping")

        # Archive the files and metadata
        if not args.dry_run:

            if par['collate1d']['archive_root'] is not None:
                metadata_root = par['collate1d']['archive_root']
                copy = True

                # If we're archiving, always copy the spec2d files even if
                # they weren't needed for collating
                if len(spec2d_files) == 0:
                    spec2d_files = find_spec2d_from_spec1d(spec1d_files)
            else:
                metadata_root = os.getcwd()
                copy = False

            archive = create_archive(metadata_root, copy)

            if copy:
                spec1d_text_files, pypeit_files = find_archvie_files_from_spec1d(spec1d_files)

                archive.add(zip(spec1d_files, spec1d_text_files, pypeit_files))
                if len(spec2d_files) > 0:
                    archive.add(zip(spec2d_files, spec1d_text_files, pypeit_files))

            archive.add(successful_source_list)
            archive.save()

        total_time = datetime.now() - start_time

        msgs.info(f'Total duration: {total_time}')

        return 0


