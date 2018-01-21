# -*- coding: utf-8 -*-
"""
Created on Wed Oct 21 12:29:33 2015

@author: Numan Laanait, Suhas Somnath, Chris Smith
"""

from __future__ import division, print_function, absolute_import, unicode_literals
import os
import subprocess
import sys
from collections import Iterable
from time import time, sleep
from warnings import warn
import h5py
import numpy as np

from .microdata import MicroDataGroup, MicroDataset, MicroData
from ..__version__ import version

if sys.version_info.major == 3:
    unicode = str


class HDFwriter(object):
    def __init__(self, file_handle, cachemult=1):
        """
        Main class that simplifies writing to pycroscopy hdf5 files.

        Parameters
        ----------
        file_handle : h5py.File object or str or unicode
            h5py.File - handle to an open file in 'w' or 'r+' mode
            str or unicode - Absolute path to an unopened the hdf5 file
        cachemult : unsigned int (Optional. default = 1)
            Cache multiplier
        """
        if type(file_handle) in [str, unicode]:
            # file handle is actually a file path
            # propfaid = h5py.h5p.create(h5py.h5p.FILE_ACCESS)
            # if cachemult != 1:
            #     settings = list(propfaid.get_cache())
            #     settings[2] *= cachemult
            #     propfaid.set_cache(*settings)
            # try:
            #     fid = h5py.h5f.open(file_handle, fapl=propfaid)
            #     self.file = h5py.File(fid, mode = 'r+')
            # except IOError:
            #     #print('Unable to open file %s. \n Making a new one! \n' %(filename))
            #     fid = h5py.h5f.create(file_handle, fapl=propfaid)
            #     self.file = h5py.File(fid, mode = 'w')
            # except:
            #     raise
            try:
                self.file = h5py.File(file_handle, 'r+')
            except IOError:
                self.file = h5py.File(file_handle, 'w')
            except:
                raise

            self.path = file_handle
        elif type(file_handle) == h5py.File:
            # file handle is actually an open hdf file
            if file_handle.mode == 'r':
                raise TypeError('HDFWriter cannot work with open HDF5 files in read mode. Change to r+ or w')
            self.file = file_handle.file
            self.path = file_handle.filename

    def clear(self):
        """
        Clear h5.file of all contents

        file.clear() only removes the contents, it does not free up previously allocated space.
        To do so, it's necessary to use the h5repack command after clearing.
        Because the file must be closed and reopened, it is best to call this
        function immediately after the creation of the HDFWriter object.
        """
        warn('This is unlikely to work on Windows due to driver issues')
        self.file.clear()
        self.repack()

    def repack(self):
        """
        Uses the h5repack command to recover cleared space in an hdf5 file.
        h5repack can also be used to change chunking and compression, but these options have
        not yet been implemented here.
        """
        warn('This is unlikely to work on Windows due to driver issues')
        self.close()
        tmpfile = self.path + '.tmp'

        '''
        Repack the opened hdf5 file into a temporary file
        '''
        try:
            repack_line = ' '.join(['h5repack', '"' + self.path + '"', '"' + tmpfile + '"'])
            subprocess.check_output(repack_line,
                                    stderr=subprocess.STDOUT,
                                    shell=True)
            # Check that the file is done being modified
            sleep(0.5)
            while time() - os.stat(tmpfile).st_mtime <= 1:
                sleep(0.5)
        except subprocess.CalledProcessError as err:
            print('Could not repack hdf5 file')
            raise Exception(err.output)
        except:
            raise

        '''
        Delete the original file and move the temporary file to the originals path
        '''
        # TODO Find way to get the real OS error that works in and out of Spyder
        try:
            os.remove(self.path)
            os.rename(tmpfile, self.path)
        except:
            print('Could not copy repacked file to original path.')
            print('The original file is located {}'.format(self.path))
            print('The repacked file is located {}'.format(tmpfile))
            raise

        '''
        Open the repacked file
        '''
        self.file = h5py.File(self.path, mode='r+')

    def close(self):
        """
        Close h5.file
        """
        self.file.close()

    def delete(self):
        """
        Delete's the existing file and creates an empty new file of the same path
        """
        self.close()
        os.remove(self.path)
        self.file = h5py.File(self.path, 'w')

    def flush(self):
        """
        Flush data from memory and commit to file.
        Use this after manually inserting data into the hdf dataset
        """
        self.file.flush()

    def write(self, data, print_log=False):
        """
        Writes data into the hdf5 file and assigns data attributes such as region references.
        The tree structure is inferred from the AFMData Object.

        Parameters
        ----------
        data : Instance of MicroData
            Tree structure describing the organization of the data
        print_log : Boolean (Optional)
            Whether or not to print all log statements - use for debugging purposes

        Returns
        -------
        ref_list : List of HDF5dataset or HDF5Datagroup references
            References to the objects written
        """

        h5_file = self.file

        h5_file.attrs['Pycroscopy version'] = version

        # Checking if the data is a MicroDataGroup object
        if not isinstance(data, MicroData):
            raise TypeError('Input expected to be of type MicroData but is of type: {} \n'.format(type(data)))

        if isinstance(data, MicroDataset):
            # just want to write a single dataset:
            try:
                h5_parent = h5_file[data.parent]
            except KeyError:
                raise ValueError('Parent ({}) of provided MicroDataset ({}) does not exist in the file'
                                 .format(data.parent, data.name))
            h5_dset = HDFwriter._create_dataset(h5_parent, data, print_log=print_log)
            return [h5_dset]

        # Figuring out if the first item in MicroDataGroup tree is file or group
        if data.name == '' and data.parent == '/':
            # For file we just write the attributes
            HDFwriter._write_simple_attrs(h5_file, data.attrs, obj_type='file', print_log=print_log)
            root = h5_file.name
        else:
            # For a group we write it and its attributes
            h5_grp = self._create_group(h5_file[data.parent], data, print_log=print_log)
            root = h5_grp.name

        # Populating the tree structure recursively
        ref_list = []

        # Recursive function
        def __populate(child, parent):
            """
            Recursive function to build the tree from the top down.

            Parameters
            ----------
            child : MicroDataGroup object
                tree to be written
            parent : h5py.Group or h5py.File object
                HDF5 object to build tree under

            Returns
            -------
            ref_list : list
                list of h5py.Dataset and h5py.Group objects created when populating the file
            """
            # Update the parent attribute with the true path
            child.parent = parent

            h5_parent_group = h5_file[parent]

            if isinstance(child, MicroDataGroup):
                h5_obj = HDFwriter._create_group(h5_parent_group, child, print_log=print_log)
                # here we do the recursive function call
                for ch in child.children:
                    __populate(ch, parent + '/' + child.name)
            else:
                h5_obj = HDFwriter._create_dataset(h5_parent_group, child, print_log=print_log)

            ref_list.append(h5_obj)
            return ref_list

        # Recursive function is called at each stage beginning at the root
        for curr_child in data.children:
            __populate(curr_child, root)

        if print_log:
            print('Finished writing to h5 file.\n' +
                  'Right now you got yourself a fancy folder structure. \n' +
                  'Make sure you do some reference linking to take advantage of the full power of HDF5.')
        return ref_list

    @staticmethod
    def _create_group(h5_parent_group, micro_group, print_log=False):
        """
        Creates a h5py.Group object from the provided MicroDataGroup object under h5_new_group and writes all attributes

        Parameters
        ----------
        h5_parent_group : h5py.Group object
            Parent group under which the new group object will be created
        micro_group : MicroDataGroup object
            Definition for the new group
        print_log : bool, optional. Default=False
            Whether or not to print debugging statements

        Returns
        -------
        h5_new_group : h5py.Group
            The newly created group
        """
        assert isinstance(micro_group, MicroDataGroup)
        assert isinstance(h5_parent_group, h5py.Group)

        h5_file = h5_parent_group.file

        # First complete the name of the group by adding the index suffix
        if micro_group.indexed:
            previous = np.where([micro_group.name in key for key in h5_parent_group.keys()])[0]
            if len(previous) == 0:
                index = 0
            else:
                last = h5_parent_group.keys()[previous[-1]]
                index = int(last.split('_')[-1]) + 1
            micro_group.name += '{:03d}'.format(index)

        # Now, try to write the group
        try:
            h5_new_group = h5_parent_group.create_group(micro_group.name)
            if print_log:
                print('Created Group {}'.format(h5_new_group.name))
        except ValueError:
            h5_new_group = h5_parent_group[micro_group.name]
            if print_log:
                print('Found Group already exists {}'.format(h5_new_group.name))
        except:
            h5_file.flush()
            h5_file.close()
            raise

        # Write attributes
        HDFwriter._write_simple_attrs(h5_new_group, micro_group.attrs, 'group', print_log=print_log)

        return h5_new_group

    @staticmethod
    def _write_simple_attrs(h5_obj, attrs, obj_type='', print_log=False):
        """
        Writes attributes to a h5py object

        Parameters
        ----------
        h5_obj : h5py.File, h5py.Group, or h5py.Dataset object
            h5py object to which the attributes will be written to
        attrs : dict
            Dictionary containing the attributes as key-value pairs
        obj_type : str / unicode, optional. Default = ''
            type of h5py.obj. Examples include 'group', 'file', 'dataset
        print_log : bool, optional. Default=False
            Whether or not to print debugging statements
        """
        assert isinstance(attrs, dict)
        assert type(h5_obj) in [h5py.File, h5py.Group, h5py.Dataset]

        for key, val in attrs.items():
            if val is None:
                continue
            if print_log:
                print('Writing attribute: {} with value: {}'.format(key, val))
                h5_obj.attrs[key] = clean_string_att(val)
        if print_log:
            print('Wrote all (simple) attributes to {}: {}\n'.format(obj_type, h5_obj.name.split('/')[-1]))

    @staticmethod
    def _create_simple_dset(h5_group, microdset):
        """
        Creates a simple h5py.Dataset object in the file. This is for those cases where the dataset contains
        small data matrices of known shape and value

        Parameters
        ----------
        h5_group : h5py.File or h5py.Group object
            Parent under which this dataset will be created
        microdset : MicroDataset object
            Definition for the dataset

        Returns
        -------
        h5_dset : h5py.Dataset object
            Newly created datset object
        """
        assert isinstance(microdset, MicroDataset)
        assert isinstance(h5_group, h5py.Group)

        h5_dset = h5_group.create_dataset(microdset.name,
                                          data=microdset.data,
                                          compression=microdset.compression,
                                          dtype=microdset.data.dtype,
                                          chunks=microdset.chunking)
        return h5_dset

    @staticmethod
    def _create_empty_dset(h5_group, microdset):
        """
        Creates a h5py.Dataset object in the file. This is for those cases where the dataset is expected to be
        large and its contents cannot be held in memory. This function creates an empty dataset that can be filled in
        manually / incrementally

        Parameters
        ----------
        h5_group : h5py.File or h5py.Group object
            Parent under which this dataset will be created
        microdset : MicroDataset object
            Definition for the dataset

        Returns
        -------
        h5_dset : h5py.Dataset object
            Newly created datset object
        """
        assert isinstance(microdset, MicroDataset)
        assert isinstance(h5_group, h5py.Group)

        h5_dset = h5_group.create_dataset(microdset.name, microdset.maxshape,
                                          compression=microdset.compression,
                                          dtype=microdset.dtype,
                                          chunks=microdset.chunking)
        return h5_dset

    @staticmethod
    def _create_resizeable_dset(h5_group, microdset):
        """
        Creates a simple h5py.Dataset object in the file. This is for those datasets whose dimensions in one or more
        dimensions are not known at the time of creation.

        Parameters
        ----------
        h5_group : h5py.File or h5py.Group object
            Parent under which this dataset will be created
        microdset : MicroDataset object
            Definition for the dataset

        Returns
        -------
        h5_dset : h5py.Dataset object
            Newly created datset object
        """
        assert isinstance(microdset, MicroDataset)
        assert isinstance(h5_group, h5py.Group)

        max_shape = tuple([None for _ in range(len(microdset.data.shape))])

        h5_dset = h5_group.create_dataset(microdset.name,
                                          data=microdset.data,
                                          compression=microdset.compression,
                                          dtype=microdset.data.dtype,
                                          chunks=microdset.chunking,
                                          maxshape=max_shape)
        return h5_dset

    @staticmethod
    def _create_dataset(h5_group, microdset, print_log=False):
        """
        Creates a h5py.Dataset object in the file. This function handles all three kinds of dataset cases

        Parameters
        ----------
        h5_group : h5py.File or h5py.Group object
            Parent under which this dataset will be created
        microdset : MicroDataset object
            Definition for the dataset
        print_log : bool, optional. Default=False
            Whether or not to print debugging statements

        Returns
        -------
        h5_dset : h5py.Dataset object
            Newly created datset object
        """
        assert isinstance(microdset, MicroDataset)
        assert isinstance(h5_group, h5py.Group)

        h5_file = h5_group.file

        if microdset.name in h5_group.keys():
            raise ValueError('Dataset named {} already exists in group!'.format(h5_group[microdset.name].name))

        def __create_dset(h5_group, microdset, build_func):
            try:
                h5_dset = build_func(h5_group, microdset)
            except:
                h5_file.flush()
                h5_file.close()
                raise
            return h5_dset

        if not microdset.resizable:
            if not bool(microdset.maxshape):
                # finite sized dataset and maxshape is not provided
                # Typically for small / ancillary datasets
                h5_dset = __create_dset(h5_group, microdset, HDFwriter._create_simple_dset)
            else:
                # In many cases, we DON'T need resizable datasets but we know the max-size
                # Here, we only allocate the space. The provided data is ignored
                h5_dset = __create_dset(h5_group, microdset, HDFwriter._create_empty_dset)
        else:
            # Resizable but the written files are significantly larger
            h5_dset = __create_dset(h5_group, microdset, HDFwriter._create_resizeable_dset)

        if print_log:
            print('Created Dataset {}'.format(h5_dset.name))

        HDFwriter._write_dset_attributes(h5_dset, microdset.attrs, print_log=print_log)

        return h5_dset

    @staticmethod
    def _write_dset_attributes(h5_dset, attrs, print_log=False):
        """
        Writes attributes to a h5py dataset

        Parameters
        ----------
        h5_dset : h5py.Dataset object
            h5py dataset to which the attributes will be written to.
            This function handles region references as well
        attrs : dict
            Dictionary containing the attributes as key-value pairs
        print_log : bool, optional. Default=False
            Whether or not to print debugging statements
        """
        assert isinstance(attrs, dict)
        assert isinstance(h5_dset, h5py.Dataset)

        # First, set aside the complicated attribute(s)
        labels = attrs.pop('labels', None)

        # Next, write the simple ones using a centralized function
        HDFwriter._write_simple_attrs(h5_dset, attrs, obj_type='dataset', print_log=print_log)

        if labels is None:
            if print_log:
                print('Finished writing all attributes of dataset')
            return

        # Now, handle the region references attribute:
        HDFwriter.__write_region_references(h5_dset, labels, print_log=print_log)
        '''
        Next, write these label names as an attribute called labels
        Now make an attribute called 'labels' that is a list of strings 
        First ascertain the dimension of the slicing:
        '''
        found_dim = False
        for dimen, slice_obj in enumerate(list(labels.values())[0]):
            # We make the assumption that checking the start is sufficient
            if slice_obj.start is not None:
                found_dim = True
                break
        if found_dim:
            headers = [None] * len(labels)  # The list that will hold all the names
            for col_name in labels.keys():
                headers[labels[col_name][dimen].start] = col_name
            if print_log:
                print('Writing header attributes: {}'.format('labels'))
            # Now write the list of col / row names as an attribute:
            h5_dset.attrs['labels'] = clean_string_att(headers)
        else:
            warn('Unable to write region labels for %s' % (h5_dset.name.split('/')[-1]))

        if print_log:
            print('Wrote Region References of Dataset %s' % (h5_dset.name.split('/')[-1]))

    @staticmethod
    def __write_region_references(h5_dset, slices, print_log=False):
        """
        Creates attributes of a h5py.Dataset that refer to regions in the dataset

        Parameters
        ----------
        h5_dset : h5.Dataset instance
            Dataset to which region references will be added as attributes
        slices : dict
            The slicing information must be formatted using tuples of slice objects.
            For example {'region_1':(slice(None, None), slice (0,1))}
        print_log : Boolean (Optional. Default = False)
            Whether or not to print status messages
        """
        assert isinstance(slices, dict)
        assert isinstance(h5_dset, h5py.Dataset)

        if print_log:
            print('Starting to write Region References to Dataset', h5_dset.name, 'of shape:', h5_dset.shape)
        for sl in slices.keys():
            if print_log:
                print('About to write region reference:', sl, ':', slices[sl])
            if len(slices[sl]) == len(h5_dset.shape):
                h5_dset.attrs[sl] = h5_dset.regionref[slices[sl]]
                if print_log:
                    print('Wrote Region Reference:%s' % sl)
            else:
                warn(
                    'Region reference %s could not be written since the object size was not equal to the dimensions of'
                    ' the dataset' % sl)
                raise ValueError


def clean_string_att(att_val):
    """
    Replaces any unicode objects within lists with their string counterparts to ensure compatibility with python 3.
    If the attribute is indeed a list of unicodes, the changes will be made in-place

    Parameters
    ----------
    att_val : object
        Attribute object

    Returns
    -------
    att_val : object
        Attribute object
    """
    try:
        if isinstance(att_val, Iterable):
            if type(att_val) in [unicode, str]:
                return att_val
            elif np.any([type(x) in [str, unicode, bytes] for x in att_val]):
                return np.array(att_val, dtype='S')
        if type(att_val) == np.str_:
            return str(att_val)
        return att_val
    except TypeError:
        raise TypeError('Failed to clean: {}'.format(att_val))
