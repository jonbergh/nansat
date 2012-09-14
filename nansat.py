# Name:    nansat.py
# Purpose: main file of the NANSAT module.
#
# Authors:      Asuka Yamakava, Anton Korosov, Knut-Frode Dagestad
#
# Created:     29.06.2011
# Copyright:   (c) NERSC 2012
# Licence:
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details:
# http://www.gnu.org/licenses/
import os.path
import dateutil.parser

try:
    from matplotlib import cm
    from numpy import arange
except:
    pass

from domain import Domain
from vrt import *
from figure import *
from nansat_tools import add_logger, Node

import scipy.stats.stats as st

class GDALError(Error):
    '''Error from GDAL '''
    pass


class DataError(Error):
    '''Error for data.
        e.g. : empty pixel value array in get_pixelValueRange()'''
    pass

class ProjectionError(Error):
    '''Error for reprojection.'''
    pass

class Nansat(Domain):
    '''Main of Nansat

    Construct Nansat object that consist of
        basic dataset information (fileName, dataset, metadata etc..),
        VRT file which points to orignal file with satellite data and
        is saved in an XML format in memory (GDAL VSI).
    '''
    def __init__(self, fileName="", mapperName="", domain=None,
                 array=None, parameters=None, logLevel=None):
        '''Construct Nansat object

        Open GDAL dataset,
        Read metadata,
        Generate GDAL VRT file with mapping of variables in memory
        Create logger

        Parameters
        ----------
        fileName : string
            location of the file
        mapperName : string, optional
            "ASAR", "hirlam", "merisL1", "merisL2", "ncep", "radarsat2",
            "seawifsL2" are currently available.  (27.01.2012)
        domain : domain object
        array : numpy array
        parameters: dictionary
            metadata for array band
        logLevel: int, optional, default: logging.DEBUG (30)
            Level of logging. See: http://docs.python.org/howto/logging.html

        Creates
        --------
        self.mapperList: list of file names
            list of available working mappers
        self.fileName : file name
            set file name given by the argument
        self.raw : VRT object
            set VRT object with VRT dataset with mapping of variables
        self.vrt : VRT object
            Copy of self.raw
        self.logger: logging.Logger
            logger for output debugging info
        self.name: string
            name of object (for writing KML)

        Raises
        ------
            GDALError: occurs when the dataset is None or "".

        '''
        # checkt the arguments
        if fileName=="" and domain is None:
            raise OptionError("Either fileName or domain is required.")

        # set attributes
        # create logger
        self.logger = add_logger('Nansat', logLevel)

        # empty dict of VRTs with added bands
        self.addedBands = {}

        # all available mappers
        self.mapperList = [
              'mapper_ASAR.py',
              'mapper_hirlam.py',
              'mapper_merisL1.py',
              'mapper_merisL2.py',
              'mapper_modisL1.py',
              'mapper_ncep.py',
              'mapper_radarsat2.py',
              'mapper_MOD44W.py',
              'mapper_modisL2NRT.py',
              'mapper_geostationary',
              'mapper_landsat.py',
              'mapper_NetCDF.py',
              'mapper_opendap.py'
              ]

        self.logger.debug('Mappers: ' + str(self.mapperList))

        # set input file name
        self.fileName = fileName
        # name, for compatibility with some Domain methods
        self.name = os.path.basename(fileName)
        self.path = os.path.dirname(fileName)

        self.latVRT = None
        self.lonVRT = None

        # create self from a file using mapper or...
        if fileName != "":
            # Make original VRT object with mapping of variables
            self.raw = self._get_mapper(mapperName)
            # Set current VRT object
            self.vrt = self.raw.copy()
        # create using array, domain, and parameters
        else:
            # Get vrt from domain
            self.raw = VRT(gdalDataset=domain.vrt.dataset)
            # Set current VRT object
            #self.vrt = self.raw.copy()
            self.vrt = VRT(gdalDataset=domain.vrt.dataset)
            if array is not None:
                # add a band from array
                self.add_band(array=array, parameters=parameters)


        self.logger.debug('Object created from %s ' % self.fileName)

    def __getitem__(self, bandID):
        ''' Returns the band as a NumPy array, by overloading []
        Parameters:
        -----------
            bandID: int or str
                If int, array from band with number <bandID> is returned
                If string, array from band with metadata 'BandName' equal to
                <bandID> is returned
        Returns
        -------
            self.get_GDALRasterBand(bandID).ReadAsArray(): NumPy array

        '''
        # get band
        band = self.get_GDALRasterBand(bandID)
        # get scale and offset
        scale = float(band.GetMetadata().get('scale', '1'))
        offset = float(band.GetMetadata().get('offset', '0'))
        expression = band.GetMetadata().get('expression', '')
        # get data
        bandData = band.ReadAsArray()
        # perform scaling if necessary
        if scale != 1 or offset != 0:
            bandData = bandData * scale + offset
        # execute expression if any
        if expression != '':
            bandData = eval(expression)

        return bandData

    def __repr__(self):
        '''Creates string basic info about the Nansat object

        '''
        outString = self.fileName + '\n'
        outString += '-' * 40 + '\n'
        outString += self.list_bands(False)
        outString += '-' * 40 + '\n'
        outString += Domain.__repr__(self)
        return outString

    def add_band(self, fileName=None, vrt=None, bandID=1, array=None, parameters=None, resamplingAlg=1):
        '''Add band from the array to self.vrt

        Create VRT object which contains VRT and RAW binary file and append it
        to self.addedBands
        Create new band in self.raw which points to this vrt

        NB!: Adding band is possible to raw (nonprojected, nonresized) images
        only. Adding band will cancel any previous repoject() or resize()

        Parameters
        ----------
            fileName: name of the file to add band from
            vrt: VRT object to add band from
            bandID: number of the band from fileName or from vrt to be added
            array : Numpy array with band data
            parameters: dictionary with band parameters: wkv, name, etc.
            resamplingAlg: 0, 1, 2 stands for nearest, bilinear, cubic

        Modifies
        --------
            Creates VRT object with VRT-file and RAW-file
            Adds band to the self.vrt

        '''
        # None => {} in input p
        if parameters is None:
            parameters = {}

        # default added vrt, source bandNumber and metadata
        vrt2add = None
        bandNumber = None
        p2add = {}

        # get band from input file name
        if fileName is not None:
            # create temporary nansat object
            n = Nansat(fileName)
            # reproject onto current grid
            n.reproject(self)
            # get vrt to be added
            vrt2add = n.vrt
            # get band metadata
            bandNumber = n._get_band_number(bandID)
            p2add = n.get_metadata(bandID=bandID)

        # get band from input VRT
        if vrt is not None:
            # get VRT to be added
            vrt2add = vrt
            # get band metadata
            bandNumber = bandID
            p2add = vrt.dataset.GetRasterBand(bandID).GetMetadata()

        # get band from input array
        if array is not None:
            if array.shape == self.shape():
                # create VRT from array
                vrt2add = VRT(array=array)
            else:
                # create VRT from resized array
                srcVRT = VRT(array=array)
                vrt2add = srcVRT.resized(self.shape()[1], self.shape()[0],
                                         resamplingAlg)
            # set parameters
            bandNumber = 1

        # add parameters from input
        for pKey in parameters:
            p2add[pKey] = parameters[pKey]

        # add the array band into self.vrt and get bandName
        bandName = self.raw._create_band({'SourceFilename': vrt2add.fileName,
                                          'SourceBand': bandNumber}, p2add)
        # add VRT with the band to the dictionary
        # (not to loose the VRT object and VRT file in memory)
        self.addedBands[bandName] = vrt2add
        self.raw.dataset.FlushCache() # required after adding bands
        # copy raw VRT object to the current vrt
        self.vrt = self.raw.copy()

    def bands(self):
        ''' Make a dictionary with all bands metadata

        Returns:
        --------
        b: dictionary: key = N, value = dict with all band metadata
        '''
        b = {}
        for iBand in range(self.vrt.dataset.RasterCount):
            b[iBand+1] = self.get_metadata(bandID=iBand+1)

        return b

    def export(self, fileName, bands=None, rmMetadata=[], addGeoloc=True, addGCPs=True):
        '''Create a netCDF file

        Parameters
        ----------
            fileName: output file name
            rmMetadata: list with metadata names to remove before export.
                e.g. ['BandName', 'colormap', 'source', 'sourceBands']

        Modifies
        --------
            Create a netCDF file

            self.vrt.dataset : VRT dataset of VRT object
                dataType in each VRTRasterBand is modified from GDAL dataType
                to netCDF dataType. The subelements "Offset" and "Scale"
                are added for each VRTRasterBand if need be.

        !! NB !!
        --------
            If number of bands is more than one,
            serial numbers are added at the end of each band name.

            It is possible to fix it by changing
            line.4605 in GDAL/frmts/netcdf/netcdfdataset.cpp :

            "if( nBands > 1 ) sprintf(szBandName,"%s%d",tmpMetadata,iBand);"
            --> "if( nBands > 1 ) sprintf(szBandName,"%s",tmpMetadata);"

        '''
        # Create new VRT object which will be used for export
        if bands is None:
            # if <bands> are not specified use all bands and make full copy
            exportVRT = self.vrt.copy()
        else:
            # if list of bands is given, make shallow copy of self.VRT
            # and add only those bands
            exportVRT = VRT(gdalDataset=self.vrt.dataset, geolocation=self.vrt.geoloc)
            allBands = self.bands()
            for bandID in bands:
                bandNumber = self._get_band_number(bandID)
                bandMetadata = allBands[bandNumber]
                bandMetadata.pop('SourceFilename')
                bandMetadata.pop('SourceBand')
                exportVRT._create_band({'SourceFilename': self.vrt.fileName,
                                        'SourceBand': bandNumber},
                                       bandMetadata)

        # Change the element from GDAL datatype to NetCDF data type
        node0 = Node.create(exportVRT.read_xml())
        for iBand in node0.nodeList("VRTRasterBand"):
            dataType = iBand.getAttribute("dataType")
            dataType = {"UInt16": "Int16", "CInt16": "Int16",
                        "UInt32": "Int32",
                        "CFloat32": "Float32", "CFloat64": "Float64"
                        }.get(dataType, dataType)
            iBand.replaceAttribute("dataType", dataType)
        exportVRT.write_xml(str(node0.rawxml()))

        # add bands with geolocation to the VRT
        if len(exportVRT.geoloc.d) > 0:
            exportVRT._create_band(
                {'SourceFilename': self.vrt.geoloc.d['X_DATASET'],
                 'SourceBand': int(self.vrt.geoloc.d['X_BAND'])},
                {'wkv': 'longitude',
                 'BandName': 'GEOLOCATION_X_DATASET'})

            exportVRT._create_band(
                {'SourceFilename': self.vrt.geoloc.d['Y_DATASET'],
                 'SourceBand': int(self.vrt.geoloc.d['Y_BAND'])},
                {'wkv': 'latitude',
                 'BandName': 'GEOLOCATION_Y_DATASET'})
                 
        exportVRT._add_gcp_metadata()

        # add projection metadata
        srs = self.vrt.dataset.GetProjection()
        exportVRT.dataset.SetMetadataItem('NANSAT_Projection', srs.replace(",", "|").replace('"', "&"))

        # add GeoTransform metadata
        geoTransformStr = str(self.vrt.dataset.GetGeoTransform()).replace(',','|')
        exportVRT.dataset.SetMetadataItem('NANSAT_GeoTransform', geoTransformStr)

        # manage metadata for each band
        for iBand in range(exportVRT.dataset.RasterCount):
            band = exportVRT.dataset.GetRasterBand(iBand + 1)
            bandMetadata = band.GetMetadata()
            # set NETCDF_VARNAME
            try:
                bandMetadata['NETCDF_VARNAME'] = bandMetadata["BandName"]
            except:
                self.logger.warning('Unable to set NETCDF_VARNAME for band %d'
                                    % (iBand+1))
            # remove unwanted metadata from bands
            for rmMeta in rmMetadata:
                try:
                    bandMetadata.pop(rmMeta)
                except:
                    self.logger.info(
                        'Unable to remove metadata %s from band %d' %
                         (rmMeta, iBand + 1))
            band.SetMetadata(bandMetadata)
        # remove unwanted global metadata
        globMetadata = exportVRT.dataset.GetMetadata()
        for rmMeta in rmMetadata:
            try:
                globMetadata.pop(rmMeta)
            except:
                self.logger.info('Global metadata %s not found' % rmMeta)
        exportVRT.dataset.SetMetadata(globMetadata)
        # Create a NetCDF file
        dataset = gdal.GetDriverByName("netCDF").CreateCopy(fileName,
                                                            exportVRT.dataset)

    def resize(self, factor=1, width=None, height=None, method="average"):
        '''Proportional resize of the dataset.

        The dataset is resized as (xSize*factor, ySize*factor) or
        (width, calulated height) or (calculated width, height).
        self.vrt is rewritten to the the downscaled sizes.
        If GCPs are given in a dataset, they are also rewritten.
        If resize() is called without any parameters resizing/reprojection is
        cancelled.


        Parameters
        ----------
        Either factor, or width, or height should be given:
            factor: float, optional, default=1
            width: int, optional
            height: int, optional
            method: "average" (default) or "subsample" (= nearest neighbor),
                    optional
        Modifies
        --------
            self.vrt.dataset : VRT dataset of VRT object
                raster size are modified to downscaled size.
                If GCPs are given in the dataset, they are also overwritten.
        Raises
        ------
            OptionError: occurs when method is not "average" or "subsample"

        '''
        # resize back to original size/setting
        if factor == 1 and width is None and height is None:
            self.vrt = self.raw.copy()
            return

        # estimate factor if width or height is given
        if width is not None:
            factor = float(width) / float(self.vrt.dataset.RasterXSize)
        if height is not None:
            factor = float(height) / float(self.vrt.dataset.RasterYSize)

        if not (method == "average" or method == "subsample"):
            raise OptionError("method should be 'average' or 'subsample'")

        # Get XML content from VRT-file
        vrtXML = self.vrt.read_xml()

        node0 = Node.create(vrtXML)
        rasterXSize = int(float(node0.getAttribute("rasterXSize")) * factor)
        rasterYSize = int(float(node0.getAttribute("rasterYSize")) * factor)
        self.logger.info('New size/factor: (%f, %f)/%f' %
                        (rasterXSize, rasterYSize, factor))
        node0.replaceAttribute("rasterXSize", str(rasterXSize))
        node0.replaceAttribute("rasterYSize", str(rasterYSize))

        for iNode in node0.nodeList("VRTRasterBand"):
            #for sourceName in ["ComplexSource", "SimpleSource"]:
            #    for iNode2 in iNode.nodeList(sourceName):
            #        iNodeDstRect = iNode2.node("DstRect")
            #        iNodeDstRect.replaceAttribute("xSize", str(rasterXSize))
            #        iNodeDstRect.replaceAttribute("ySize", str(rasterYSize))
            # if method="average", overwrite "ComplexSource" to "AveragedSource"
            if method == "average":
                iNode.replaceTag("ComplexSource", "AveragedSource")
                iNode.replaceTag("SimpleSource", "AveragedSource")

        # Edit GCPs to correspond to the downscaled size
        if node0.node("GCPList"):
            for iNode in node0.node("GCPList").nodeList("GCP"):
                pxl = float(iNode.getAttribute("Pixel")) * factor
                if pxl > float(rasterXSize):
                    pxl = rasterXSize
                iNode.replaceAttribute("Pixel", str(pxl))
                lin = float(iNode.getAttribute("Line")) * factor
                if lin > float(rasterYSize):
                    lin = rasterYSize
                iNode.replaceAttribute("Line", str(lin))

        # Write the modified elemements into VRT
        self.vrt.write_xml(str(node0.rawxml()))

    def get_GDALRasterBand(self, bandID=1):
        ''' Get a GDALRasterBand of a given Nansat object.

        Get a GDALRasterBand specified by the argument.

        If a bandID is given, secify a bandID based on it.
        Otherwise check if the given bandID is proper.
        Get a GDALRasterBand from vrt.

        Parameters
        ----------
            bandID: serial number or string, optional (default is 1)
                if number - a band number of the band to fetch
                if string bandID = {'BandName': bandID}

        Returns
        -------
            self.vrt.dataset.GetRasterBand: a GDAL RasterBand

        Example
        -------
            b = get_GDALRasterBand(1)
            b = get_GDALRasterBand('sigma0')
        '''
        # get band number
        bandNumber = self._get_band_number(bandID)
        # the GDAL RasterBand of the corresponding band is returned
        return self.vrt.dataset.GetRasterBand(bandNumber)

    def list_bands(self, doPrint=True):
        ''' Show band information of the given Nansat object

        Show serial number, longName, name and all parameters
        for each band in the metadata of the given Nansat object.

        Parameters:
        -----------
            doPrint: boolean, optional, default=True
                do print, otherwise it is returned as string
        Returns:
        --------
            outString: String
                formatted string with bands info
        '''
        # get dictionary of bands metadata
        bands = self.bands()
        outString = ''

        for b in bands:
            # print band number, name
            outString += "Band : %d %s\n" % (b, bands[b].get('BandName', ''))
            # print band metadata
            for i in bands[b]:
                outString += "  %s: %s\n" % (i, bands[b][i])
        if doPrint:
            # print to screeen
            print outString
        else:
            return outString

    def reproject(self, dstDomain=None, resamplingAlg=0):
        ''' Reproject the object based on the given Domain

        Warp the raw VRT using AutoCreateWarpedVRT() using projection
        from the Domain.
        Modify XML content of the warped vrt using the Domain parameters.
        Generate warpedVRT and replace self.vrt with warpedVRT.

        Parameters
        ----------
            dstDomain: domain
                destination Domain where projection and resolution are set
            resamplingAlg: int
                0, 1 or 2 stand for NearestNeigbour, Bilinear, Cubic

        Modifies
        --------
            self.vrt: VRT object with VRT dataset
                replaced to warpedVRT dataset

        Raises
        ------
            ProjectionError: occurs when the projection of the target data
            is None.
            AttributeError: occurs when it is impossible to get warpedVRT.

        See Also
        --------
            http://www.gdal.org/gdalwarp.html

        '''
        # dereproject and quit
        if dstDomain is None:
            self.vrt = self.raw.copy()
            return

        # get projection of destination dataset
        dstSRS = dstDomain.vrt.dataset.GetProjection()

        # get destination GCPs
        dstGCPs = dstDomain.vrt.dataset.GetGCPs()
        if len(dstGCPs) > 0:
            # get projection of destination GCPs
            dstSRS = dstDomain.vrt.dataset.GetGCPProjection()

        # create Warped VRT
        warpedVRT = self.raw.create_warped_vrt(
                    dstSRS=dstSRS, dstGCPs=dstGCPs, resamplingAlg=resamplingAlg,
                    xSize=dstDomain.vrt.dataset.RasterXSize,
                    ySize=dstDomain.vrt.dataset.RasterYSize,
                    geoTransform=dstDomain.vrt.dataset.GetGeoTransform())

        self.vrt = warpedVRT

    def watermask(self, mod44path=None, dstDomain=None):
        ''' Create numpy array with watermask (water=1, land=0)

        250 meters resolution watermask from MODIS 44W Product:
        http://www.glcf.umd.edu/data/watermask/

        Watermask is stored as tiles in TIF(LZW) format and a VRT file
        All files are stored in one directory.
        A tarball with compressed TIF and VRT files should be additionally
        downloaded from the Nansat wiki:
        https://svn.nersc.no/nansat/wiki/Nansat/Data/Watermask

        The method:
            Gets the directory either from input parameter or from environment
            variable MOD44WPATH
            Open Nansat object from the VRT file
            Reprojects the watermask onto the current object using reproject()
            or reproject_on_jcps()
            Returns the reprojected Nansat object

        Parameters:
        -----------
            mod44path : string, optional, default=None
                path with MOD44W Products and a VRT file

        Returns:
        --------
            watermask : Nansat object with water mask in current projection

        See also:
        ---------
            250 meters resolution watermask from MODIS 44W Product:
            http://www.glcf.umd.edu/data/watermask/
        '''
        mod44DataExist = True
        # check if path is given in input param or in environment
        if mod44path is None:
            mod44path = os.getenv('MOD44WPATH')
        if mod44path is None:
            mod44DataExist = False
        # check if VRT file exist
        elif not os.path.exists(mod44path + '/MOD44W.vrt'):
            mod44DataExist = False
        self.logger.debug('MODPATH: %s' % mod44path)

        if not mod44DataExist:
            # MOD44W data does not exist generate empty matrix
            watermask = np.zeros(self.vrt.dataset.RasterXSize,
                                 self.vrt.dataset.RasterYSize)
        else:
            # MOD44W data does exist: open the VRT file in Nansat
            watermask = Nansat(mod44path + '/MOD44W.vrt')
            # reproject on self or given Domain
            if dstDomain is None:
                watermask.reproject(self)
            else:
                watermask.reproject(dstDomain)

        return watermask

    def write_figure(self, fileName=None, bands=1, clim=None, **kwargs):

        ''' Save a raster band to a figure in grapfical format.

        Get numpy array from the band(s) and band information specified
        either by given band number or band id.
        -- If three bands are given, merge them and create PIL image.
        -- If one band is given, create indexed image
        Create Figure object and:
        Adjust the array brightness and contrast using the given min/max or
        histogram.
        Apply logarithmic scaling of color tone.
        Generate and append legend.
        Save the PIL output image in PNG or any other graphical fornat.

        Parameters
        ----------
            fileName: string, optional
                Output file name. if one of extensions "png", "PNG", "tif",
                "TIF", "bmp", "BMP", "jpg", "JPG", "jpeg", "JPEG" is included,
                specified file is crated. otherwise, "png" file is created.
                if None, the figure object is returned.
                if True, the figure is shown
            bands : integer or string or list (elements are integer or string), default = 1
                the size of the list has to be 1 or 3.
                if the size is 3, RGB image is created based on the
                three bands.
                Then the first element is Red, the second is Green,
                and the third is Blue.
            clim : list with two elements or 'hist' to specify range of
                colormap
                None (default): min/max values are fetched from WKV,
                fallback-'hist'
                [min, max] : min and max are numbers, or
                [[min, min, min], [max, max, max]]: three bands used
                'hist' : a histogram is used to calculate min and max values
            **kwargs : parameters for Figure(). See figure.Figure()

        Modifies
        --------
            if fileName is specified, creates image file

        Returns
        ------
            Figure object

        Example:
        --------
        #write only indexed image, color limits from WKV or from histogram
        n.write_figure('test.jpg')
        #write only RGB image, color limits from histogram
        n.write_figure('test_rgb_hist.jpg', clim='hist', bands=[1, 2, 3])
        #write indexed image, apply log scaling and gamma correction,
        #add legend and type in title 'Title', increase font size and put 15
        tics
        n.write_figure('r09_log3_leg.jpg', logarithm=True, legend=True,
                                gamma=3, titleString='Title', fontSize=30,
                                numOfTicks=15)

        See also
        ------
        Figure()
        http://www.scipy.org/Cookbook/Matplotlib/Show_colormaps

        '''
        # convert <bands> from integer, or string, or list of strings
        # into list of integers
        if isinstance(bands, list):
            for i, band in enumerate(bands):
                bands[i] = self._get_band_number(band)
        else:
            bands = [self._get_band_number(bands)]

        # == create 3D ARRAY ==                
        array = None
        for band in bands:
            # get array from band and reshape to (1,height,width)
            iArray = self[band]
            iArray = iArray.reshape(1, iArray.shape[0], iArray.shape[1])
            # create new 3D array or append band
            if array is None:
                array = iArray
            else:
                array = np.append(array, iArray, axis=0)
        
        # == CREATE FIGURE object and parse input parameters ==
        fig = Figure(array, **kwargs)
        array = None

        # == PREPARE cmin/cmax ==
        # try to get clim from WKV if it is not given
        # if failed clim will be evaluated from histogram
        if clim is None:
            clim = [[], []]
            for i, iBand in enumerate(bands):
                try:
                    defValue = (self.vrt.dataset.GetRasterBand(iBand).
                                GetMetadataItem("minmax").split(" "))
                except:
                    clim = 'hist'
                    break
                clim[0].append(float(defValue[0]))
                clim[1].append(float(defValue[1]))

        # Estimate color min/max from histogram
        if clim == 'hist':
            clim = fig.clim_from_histogram()

        # modify clim to the proper shape [[min], [max]]
        # or [[min, min, min], [max, max, max]]
        if (len(clim) == 2 and
           ((isinstance(clim[0], float)) or (isinstance(clim[0], int))) and
           ((isinstance(clim[1], float)) or (isinstance(clim[1], int)))):
            clim = [[clim[0]], [clim[1]]]

        # if the len(clim) is not same as len(bands), the 1st element is used.
        for i in range(2):
            if len(clim[i]) != len(bands):
                clim[i] = [clim[i][0]] * len(bands)

        self.logger.info('clim: %s ' % clim)

        # == PREPARE caption ==
        # get longName and units from vrt
        band = self.get_GDALRasterBand(bands[0])
        longName = band.GetMetadata().get("long_name", '')
        units = band.GetMetadata().get("units", '')

        # make caption
        caption = longName + ' [' + units + ']'
        self.logger.info('caption: %s ' % caption)

        # == PROCESS figure ==
        fig.process(cmin=clim[0], cmax=clim[1], caption=caption)

        # == finally SAVE to a image file or SHOW ==
        if fileName is not None:
            if type(fileName) == bool and fileName:
                fig.pilImg.show()
            elif type(fileName) == str:
                fig.save(fileName)

        return fig

    def write_geotiffimage(self, fileName, bandID=1):
        ''' Writes an 8-bit GeoTiff image for a given band.

        The output GeoTiff image is convenient e.g. for display in a GIS tool.
        Colormap is fetched from the metadata item 'colormap'.
            Fallback colormap is 'jet'.
        Color limits are fetched from the metadata item 'minmax'.
            If 'minmax' is not specified, min and max of raster is used.

        Parameters
        ----------
            fileName: string
            bandID: integer or string(default = 1)

        AK: Two thirds of this method is done in write_figure()
        I would suggest rather to get rid of write_geotiffimage() and add
        functionality to write_figure(). E.g.:
        if DEFAULT_EXTENSION=GTiff:
            # save as tif
            # add georeference to the ouput file c.a.:
            # ds = gdal.Open(outFile, 'RW')
            # ds.SetProjection(self.vrt.dataset.GetProjection())
            # ...GeoTransform...
            # ...GCPs...


        '''
        bandNumber = self._get_band_number(bandID)
        band = self.get_GDALRasterBand(bandID)
        minmax = band.GetMetadataItem('minmax')
        # Get min and max from band histogram if not given (from wkv)
        if minmax is None:
            (rmin, rmax) = band.ComputeRasterMinMax(1)
            minmax = str(rmin) + ' ' + str(rmax)

        # Apply offset and scaling if available
        #  (not necessary when a LUT is available,
        #   and when no offset/scaling should be specified)
        try:
            offset = float(band.GetMetadataItem('offset'))
            scale = float(band.GetMetadataItem('scale'))
            minval = float(minmax.split(" ")[0])
            maxval = float(minmax.split(" ")[1])
            minmax = str((minval-offset)/scale) + ' ' + str((maxval-offset)/scale)
        except:
            pass

        # Create a temporary VRT file (no colormap yet) and convert this to 8-bit geotiff image
        # Should ideally do this directly with GDAL Python API (CreateCopy),
        # but gdal_translate provides conenient scaling and conversion to Byte
        tmpVRTFileName = fileName + '.tmp.VRT'
        self.vrt.export(tmpVRTFileName)

        # Add colormap from WKV to the VRT file
        try:
            colormap = band.GetMetadataItem('colormap')
        except:
            colormap = 'jet'
        try:
            cmap = cm.get_cmap(colormap, 256)
            cmap = cmap(arange(256))*255
            colorTable = gdal.ColorTable()
            for i in range(cmap.shape[0]):
                colorEntry = (int(cmap[i, 0]), int(cmap[i, 1]),
                    int(cmap[i, 2]), int(cmap[i, 3]))
                colorTable.SetColorEntry(i, colorEntry)
            tmpFile = gdal.Open(tmpVRTFileName)
            tmpFile.GetRasterBand(bandNumber).SetColorTable(colorTable)
            tmpFile = None
        except:
            print 'Could not add colormap; Matplotlib may not be available.'

        os.system('gdal_translate ' + tmpVRTFileName + ' ' + fileName +
            ' -b ' + str(bandNumber) + ' -ot Byte -scale ' + minmax + ' 0 255' +
            ' -co "COMPRESS=LZW"')
        os.remove(tmpVRTFileName)


    def get_time(self, bandID=None):
        ''' Get time for dataset and/or its bands

        Parameters
        ----------
            bandID: int or str (default = None)
                number or BandName
        Returns:
            time: list with datetime objects for each band.
            If time is the same for all bands, the list contains 1 item
        '''
        time = []
        for i in range(self.vrt.dataset.RasterCount):
            band = self.get_GDALRasterBand(i + 1)
            try:
                time.append(dateutil.parser.parse(
                                band.GetMetadataItem("time")))
            except:
                self.logger.debug("Band " + str(i + 1) + " has no time")
                time.append(None)

        if bandID is not None:
            bandNumber = self._get_band_number(bandID)
            return time[bandNumber - 1]
        else:
            return time

    def get_metadata(self, key=None, bandID=None):
        ''' Get metadata from self.vrt.dataset

        Parameters:
        -----------
        key: string, optional
            name of the metadata key. If not givem all metadata is returned
        bandID: int or str, optional
            number or BandName of band to get metadata from.
            If not given, global metadata is returned

        Returns:
            a string with metadata if key is given and found
            an empty string if key is given and not found
            a dictionary with all metadata if key is not given
        '''

        # get all metadata from dataset or from band
        if bandID is None:
            metadata = self.vrt.dataset.GetMetadata()
        else:
            metadata = self.get_GDALRasterBand(bandID).GetMetadata()

        # get all metadata or from a key
        if key is not None:
            metadata = metadata.get(key, None)

        return metadata

    def set_metadata(self, key='', value='', bandID=None):
        ''' Set metadata to self.raw.dataset and self.vrt.dataset

        Parameters:
        -----------
        key: string or dictionary with strings
            name of the metadata, or dictionary with metadata names, values
        value: string
            value of metadata
        bandID: int or str
            number or name of band
            Without: global metadata is set

        Modifies:
        ---------
            self.raw.dataset: sets metadata in GDAL raw dataset
            self.vrt.dataset: sets metadata in GDAL current dataset
            '''

        # set all metadata to the dataset or to the band
        if bandID is None:
            metaReceiverRAW = self.raw.dataset
            metaReceiverVRT = self.vrt.dataset
        else:
            bandNumber = self._get_band_number(bandID)

            metaReceiverRAW = self.raw.dataset.GetRasterBand(bandNumber)
            metaReceiverVRT = self.vrt.dataset.GetRasterBand(bandNumber)

        # set metadata from dictionary or from single pair key,value
        if isinstance(key, dict):
            for k in key:
                metaReceiverRAW.SetMetadataItem(k, key[k])
                metaReceiverVRT.SetMetadataItem(k, key[k])
        else:
            metaReceiverRAW.SetMetadataItem(key, value)
            metaReceiverVRT.SetMetadataItem(key, value)

    def _get_mapper(self, mapperName):
        ''' Create VRT file in memory (VSI-file) with variable mapping

        If mapperName is given, it is added as the first in the self.mapperList
        Loop over all availble mappers in mapperList to get the matching one.
        In the loop:
            If the specific error appears the mapper is not used
            and the next mapper is tested.
            Otherwise the mapper returns VRT.
        If type of the sensor is identified, add mapping variables.
        If all mapper do not fit, simply copy the input DS into a VSI/VRT

        Parameters
        ----------
        mapperName : string, optional
            "ASAR", "hurlam", "merisL1", "merisL2", "ncep", "radarsat2",
            "seawifsL2" are currently available.  (27.01.2012)

        Returns
        -------
            tmpVRT : VRT object
                tmpVRT.dataset is a GDAL VRT dataset

        Raises
        --------
            GDALError: occures if the given file cannot be opened with GDAL
            TypeError: occurs when the given driver type is not registarated
                        in the mappers.

        '''
        # open GDAL dataset. It will be parsed to all mappers for testing
        gdalDataset = gdal.Open(self.fileName)
        if gdalDataset is not None:
            # get metadata from the GDAL dataset
            metadata = gdalDataset.GetMetadata()
        else:
            metadata = None

        # add the given mapper first
        self.mapperList = ['mapper_' + mapperName] + self.mapperList

        # try to import and get VRT datasaet from all mappers. Break on success.
        # If none of the mappers worked - try generic gdal.Open
        tmpVRT = None
        # For debugging:
        """
        mapper_module = __import__('mapper_ASAR')
        tmpVRT = mapper_module.Mapper(self.fileName, gdalDataset,
                                      metadata)
        """
        # Otherwise
        for iMapper in self.mapperList:
            try:
                #get rid of .py extension
                iMapper = iMapper.replace('.py', '')
                self.logger.debug('Trying %s...' % iMapper)
                #import mapper
                mapper_module = __import__(iMapper)
                #create a Mapper object and get VRT dataset from it
                tmpVRT = mapper_module.Mapper(self.fileName,
                                              gdalDataset, metadata)
                self.logger.info('Mapper %s - success!' % iMapper)
                break
            except:
                pass
        # """
        # if no mapper fits, make simple copy of the input DS into a VSI/VRT
        if tmpVRT is None and gdalDataset is not None:
            self.logger.info('No mapper fits!')
            tmpVRT = VRT(gdalDataset=gdalDataset)
            for iBand in range(gdalDataset.RasterCount):
                tmpVRT._create_band({'SourceFilename': self.fileName,
                                     'SourceBand': iBand+1})
                tmpVRT.dataset.FlushCache()

        return tmpVRT

    def _get_pixelValue(self, val, defVal):
        if val == "":
            return defVal
        else:
            return val

    def _get_band_number(self, bandID):
        '''Return absolute band number

        Check if given band_id is valid
        Return absolute number of the band in the VRT

        Parameters
        ----------
            bandID: int or str
                if int: checks if such band exists and returns band_id
                if str: finds band with coresponding BandName

        Returns
        -------
            int, absolute band  number
        '''
        bandNumber = 0
        # if bandID is string, fetch band which has BandName == bandID
        if isinstance(bandID, str):
            bandsMeta = self.bands()
            for b in bandsMeta:
                if bandID == bandsMeta[b]['BandName']:
                    bandNumber = b

        # if given bandID is int and within the existing bands, return it
        elif (bandID >= 1 and bandID <= self.vrt.dataset.RasterCount):
            bandNumber = bandID
        # if not bandNumber found - raise error
        if bandNumber == 0:
            raise OptionError("Cannot find band %s! "
                              "bandNumber is from 1 to %s" %
                              (str(bandID), self.vrt.dataset.RasterCount))

        return bandNumber

    def mosaic(self, files=[], bands=[], **kwargs):
        '''Mosaic input files. If images overlap, calculate average

        Convert all input files into Nansat objects, reproject, get bands,
        put bands into a 3D cube, average, add averaged bands to the current
        object.

        mosaic() tries to get band 'mask' from the input files. The mask
        should have the following coding:
            0:   nodata
            1:   clouds
            2:   land
            128: values
        If it gets that band (which can be provided by some mappers or Nansat
        childs, e.g.  ModisL2NRT) it uses it to select averagable pixels
        (i.e. where mask == 128).
        If it cannot locate the band 'mask' is assumes that all pixels are
        averagebale except for thouse out of swath after reprojection.

        mosaic() adds bands to the object, so it works only with empty, or
        non-projected objects

        Parameters
        ----------
            files: list
                list of input files
            bands: list
                list of BandNames/band_numbers to be processed
            nClass: child of Nansat
                The class to be used to read input files
        '''
        # get Nansat child class
        nClass = kwargs.get('nClass', Nansat)

        # get desired shape
        dstShape = self.shape()
        self.logger.debug('dstShape: %s' % str(dstShape))

        # preallocate 2D matrices for sum, sum of squares, count of products
        # and mask
        self.logger.debug('Allocating 2D matrices')
        avgMat = {}
        stdMat = {}
        for b in bands:
            avgMat[b] = np.zeros((dstShape[0], dstShape[1]))
            stdMat[b] = np.zeros((dstShape[0], dstShape[1]))

        cntMat = np.zeros((dstShape[0], dstShape[1]))
        maskMat = np.zeros((2, dstShape[0], dstShape[1]))

        # for all input files
        for i, f in enumerate(files):
            self.logger.info('Processing %s' % f)
            # open file using Nansat or its child class
            n = nClass(f, logLevel=self.logger.level)
            # add mask band [0: nodata, 1: cloud, 2: land, 128: data]
            try:
                mask = n['mask']
            except:
                mask = 128 * np.ones(n.shape()).astype('int8')
                n.add_band(mask, parameters={'BandName': 'mask'})

            n.reproject(self)
            try:
                # get reprojected mask
                mask = n['mask']
            except:
                # if 'mask' cannot be fetched, skip the file
                self.logger.error('Cannot reproject %s!' % f)
            else:
                # add data to counting matrix
                cntMatTmp = np.zeros((dstShape[0], dstShape[1]))
                cntMatTmp[mask == 128] = 1
                cntMat += cntMatTmp
                # add data to mask matrix (maximum of 0, 1, 2, 128)
                maskMat[0, :, :] = mask
                maskMat[1, :, :] = maskMat.max(0)

                # add data to summation matrix
                for b in bands:
                    self.logger.debug('    Adding %s to sum' % b)
                    # get projected data from Nansat object
                    a = n[b]
                    # mask invalid data
                    a[mask < 128] = 0
                    # sum of valid values and squares
                    avgMat[b] += a
                    stdMat[b] += np.square(a)

        # average products
        for b in bands:
            self.logger.debug('    Averaging %s' % b)
            # get average
            avg = avgMat[b] / cntMat
            # get/set STD
            # STD = sqrt(sum((x-M)^2)/n) = sqrt((sum(x^2) - 2*mean(x)*sum(x) + sum(mean(x)^2))
            stdMat[b] = np.square((stdMat[b] - 2 * avg * avgMat[b] + np.square(avg)) / cntMat)
            # set std
            avgMat[b] = avg

        # calculate mask (max of 0, 1, 2, 128)
        maskMat = maskMat.max(0)

        self.logger.debug('Adding bands')
        # add mask band
        self.logger.debug('    mask')
        self.add_band(array=maskMat, parameters={'BandName': 'mask'})
        # add averaged bands
        for b in bands:
            self.logger.debug('    %s' % b)
            self.add_band(array=avgMat[b], parameters={'BandName': b})
            self.add_band(array=stdMat[b], parameters={'BandName': b + '_std'})

    def process(self, opts=None):
        '''Default L2 processing of Nansat object. Empty. Overloaded.'''
