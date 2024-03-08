import datajoint as dj
import matplotlib.pyplot as plt
import cv2
from typing import Optional
import numpy as np
from datetime import datetime
import inspect
import importlib
import os
import yaml 
from pathlib import Path
from element_interface.utils import find_full_path
from .readers.kpms_reader import generate_dj_config
from keypoint_moseq import (
            setup_project,
            load_config,
            load_keypoints,
            format_data,
            load_pca,
            fit_pca,
            save_pca,
            check_config_validity
        )

        
schema = dj.schema()
_linking_module = None


def activate(
    pca_schema_name: str,
    *,
    create_schema: bool = True,
    create_tables: bool = True,
    linking_module: str = None,
):
    """Activate this schema.

    Args:
        pca_schema_name (str): A string containing the name of the pca schema.
        create_schema (bool): If True (default), schema  will be created in the database.
        create_tables (bool): If True (default), tables related to the schema will be created in the database.
        linking_module (str): A string containing the module name or module containing the required dependencies to activate the schema.

    Dependencies:
    Functions:
        get_kpms_root_data_dir(): Returns absolute path for root data director(y/ies) with all behavioral recordings, as (list of) string(s)
        get_kpms_processed_data_dir(): Optional. Returns absolute path for processed data. Defaults to session video subfolder.
    """

    if isinstance(linking_module, str):
        linking_module = importlib.import_module(linking_module)
    assert inspect.ismodule(
        linking_module
    ), "The argument 'dependency' must be a module's name or a module"

    assert hasattr(
        linking_module, "get_kpms_root_data_dir"
    ), "The linking module must specify a lookup function for a root data directory"

    global _linking_module
    _linking_module = linking_module

    # activate
    schema.activate(
        pca_schema_name,
        create_schema=create_schema,
        create_tables=create_tables,
        add_objects=_linking_module.__dict__,
    )


# -------------- Functions required by the element-moseq ---------------


def get_kpms_root_data_dir() -> list:
    """Pulls relevant func from parent namespace to specify root data dir(s).

    It is recommended that all paths in DataJoint Elements stored as relative
    paths, with respect to some user-configured "root" director(y/ies). The
    root(s) may vary between data modalities and user machines. Returns a full path
    string or list of strings for possible root data directories.
    """
    root_directories = _linking_module.get_kpms_root_data_dir()
    if isinstance(root_directories, (str, Path)):
        root_directories = [root_directories]

    if (
        hasattr(_linking_module, "get_kpms_processed_data_dir")
        and get_kpms_processed_data_dir() not in root_directories
    ):
        root_directories.append(_linking_module.get_kpms_processed_data_dir())

    return root_directories


def get_kpms_processed_data_dir() -> Optional[str]:
    """Pulls relevant func from parent namespace. Defaults to KPMS's project /videos/.

    Method in parent namespace should provide a string to a directory where KPMS output
    files will be stored. If unspecified, output files will be stored in the
    session directory 'videos' folder, per DeepLabCut default.
    """
    if hasattr(_linking_module, "get_kpms_processed_data_dir"):
        return _linking_module.get_kpms_processed_data_dir()
    else:
        return None


# ----------------------------- Table declarations ----------------------


@schema
class PoseEstimationMethod(dj.Lookup):
    """Table for storing the pose estimation method used to obtain the keypoints data.

    Attributes:
        format_method (str)                : Pose estimation method.
        pose_estimation_desc (str)  : Pose estimation method description.
    """

    definition = """ 
    # Parameters used to obtain the keypoints data based on a specific pose estimation method.        
    format_method                          : char(15)         # deeplabcut, sleap, anipose, sleap-anipose, nwb, facemap,
    ---
    pose_estimation_desc            : varchar(1000)    # Optional. Pose estimation method description
    """

    contents = [
        ["deeplabcut", "`.csv` and `.h5/.hdf5` files generated by DeepLabcut analysis"],
        ["sleap", "`.slp` and `.h5/.hdf5` files generated by SLEAP analysis"],
        ["anipose", "`.csv` files generated by anipose analysis"],
        ["sleap-anipose", "`.h5/.hdf5` files generated by sleap-anipose analysis"],
        ["nwb", "`.nwb` files with Neurodata Without Borders (NWB) format"],
        ["facemap", "`.h5` files generated by Facemap analysis"],
    ]


@schema
class KeypointSet(dj.Manual):
    """Table for storing the keypoint sets and their associated videos.

    Attributes:
        kpset_id (int): Unique ID for each keypoint set.
        kpset_config_dir (str): Path relative to root data directory where the config file is located.
        kpset_videos_dir (str): Path relative to root data directory where the videos and their keypoints are located.
        kpset_description (str): Optional. User-entered description.
    """

    definition = """
    -> Session
    kpset_id                        : int
    ---
    -> PoseEstimationMethod
    kpset_config_dir               : varchar(255)  # Path relative to root data directory where the config file is located
    kpset_videos_dir                : varchar(255)  # Path relative to root data directory where the videos and their keypoints are located
    kpset_desc=''            : varchar(300)  # Optional. User-entered description
    """

    class VideoFile(dj.Part):
        """IDs and file paths of each video file.

        Atribbutes:
            video_id (int): Unique ID for each video.
            video_path (str): Filepath of each video, relative to root data directory.
        """

        definition = """
        -> master
        video_id                    : int
        ---
        video_path                  : varchar(1000) # Filepath of each video, relative to root data directory
        """


@schema
class RecordingInfo(dj.Imported):
    """Automated table to store video metadata.

    Attributes:
        KeypointSet.VideoFiles (foreign key)    : Unique ID for each video.
        px_height (smallint)                    : Height in pixels.
        px_width (smallint)                     : Width in pixels.
        nframes (int)                           : Number of frames.
        fps (int)                               : Optional. Frames per second, Hz.
        recording_datetime (datetime)           : Optional. Datetime for the start of recording.
        recording_duration (float)              : Video duration (s) from nframes / fps.
    """

    definition = """
    -> KeypointSet
    ---
    px_height                 : smallint  # Height in pixels
    px_width                  : smallint  # Width in pixels
    nframes                   : int       # Number of frames 
    fps = NULL                : int       # Optional. Frames per second, Hz
    recording_datetime = NULL : datetime  # Optional. Datetime for the start of the recording
    recording_duration        : float     # Video duration (s) from nframes / fps
    """

    @property
    def key_source(self):
        """Defines order of keys for the make function when called via `populate()`"""
        return KeypointSet & KeypointSet.VideoFiles

    def make(self, key):
        """
        Make function to populate the RecordingInfo table.

        Args:
            key (dict): Primary key from the RecordingInfo table.

        Returns:
            dict: Primary key and attributes for the RecordingInfo table.

        Raises:
        High-Level Logic:
        1. Fetches the file paths and video IDs from the KeypointSet.VideoFiles table.
        2. Iterates through the file paths and video IDs to obtain the video metadata using OpenCV.
        3. Inserts the video metadata into the RecordingInfo table.

        """

        file_paths, video_ids = (KeypointSet.VideoFiles & key).fetch(
            "video_path", "video_id"
        )

        for fp, video_id in zip(file_paths, video_ids):
            nframes = 0
            px_height, px_width, fps = None, None, None

            file_path = (find_full_path(get_kpms_root_data_dir(), fp)).as_posix()

            cap = cv2.VideoCapture(file_path)
            info = (
                int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(cap.get(cv2.CAP_PROP_FPS)),
            )
            if px_height is not None:
                assert (px_height, px_width, fps) == info
            px_height, px_width, fps = info
            nframes += int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()

            self.insert1(
                {
                    **key,
                    "video_id": video_id,
                    "px_height": px_height,
                    "px_width": px_width,
                    "nframes": nframes,
                    "fps": fps,
                    "recording_duration": nframes / fps,
                }
            )


@schema
class Bodyparts(dj.Manual):
    """Table for storing the bodyparts used in the analysis.

    Attributes:
        KeypointSet (foreign key)       : Unique ID for each keypoint set.
        bodyparts_id (int)              : Unique ID for each bodypart.
        anterior_bodyparts (longblob)   : List of strings of anterior bodyparts
        posterior_bodyparts (longblob)  : List of strings of posterior bodyparts
        use_bodyparts (longblob)        : List of strings of bodyparts to be used
    """

    definition = """
    -> KeypointSet
    bodyparts_id                : int
    ---
    bodyparts_desc=''           : varchar(1000)
    anterior_bodyparts          : blob  # List of strings of anterior bodyparts
    posterior_bodyparts         : blob  # List of strings of posterior bodyparts
    use_bodyparts               : blob  # List of strings of bodyparts to be used
    """


@schema
class PCATask(dj.Manual):
    """
    Table to define the PCA task.

    Attributes:
        KeypointSet (foreign key)       : Unique ID for each keypoint set.
        Bodyparts (foreign key)         : Unique ID for each bodypart.
        pca_task_id (int)               : Unique ID for each PCA task.
        output_dir (str)                : KPMS's output directory in config relative to root
        task_mode (str)                 : 'load': load computed analysis results, 'trigger': trigger computation
    """

    definition = """ 
    -> Bodyparts
    ---
    output_dir='' : varchar(255)             # KPMS's output directory in config relative to root
    task_mode='load' : enum('load', 'trigger') # 'load': load computed analysis results, 'trigger': trigger computation
    """

@schema
class FormattedDataset(dj.Imported): # --> TO-DO: change name for a more intuitive option
    """
    Table for storing the formatted dataset and update the config.yml by creating a new dj_config.yml in the project path (output_dir)
    """

    definition = """
    -> PCATask
    ---
    coordinates             : longblob
    confidences             : longblob             
    formatted_bodyparts     : longblob
    """

    def make(self, key):
        """
        Make function to format keypoint coordinates and confidences for inference.
        
        Args:
            key (dict): Primary key from the PCATask table.

        Returns:
            dict: Primary key and attributes for the PCATask table.

        Raises:
        
        High-Level Logic:
        
        """

        anterior_bodyparts, posterior_bodyparts, use_bodyparts = (
        Bodyparts & key
        ).fetch1(
            "anterior_bodyparts",
            "posterior_bodyparts",
            "use_bodyparts",
        )
        output_dir = (PCATask & key).fetch1("output_dir")
        task_mode = (PCATask & key).fetch1("task_mode")
        format_method = (KeypointSet & key).fetch1("format_method")
        kpset_config_dir, kpset_videos_dir = (KeypointSet & key).fetch1(
        if task_mode == "trigger":
            config = setup_project(
                    project_path, deeplabcut_config=kpset_config_path
                ) 

        elif task_mode == "load":
            config_kwargs_dict = dict(
            # ---- Build and save DLC configuration (yaml) file ----
            config = load_config(output_dir, check_if_valid=True, build_indexes=False)
            config.update(**config_kwargs_dict)
            generate_dj_config(output_dir, **config)

        # load keypoints data from deeplabcut, sleap, anipose, sleap-anipose, nwb, facemap
        coordinates, confidences, formatted_bodyparts = load_keypoints(
            filepath_pattern=kpset_videos_dir, format=format_method
        )
        
        self.insert1(
            dict(
                **key,
                coordinates=coordinates,
                confidences=confidences,
                formatted_bodyparts=formatted_bodyparts
            )
        )


@schema
class PCAFitting(dj.Computed):
    
    definition = """
    -> FormattedDataset
    ---
    pca_fitting_time=NULL    : datetime  # Time of generation of the PCA fitting analysis 
    """

    def make(self, key):
        task_mode, output_dir = (PCATask & key).fetch1("task_mode", "output_dir")

        if task_mode == "trigger":
            config = load_config(output_dir, check_if_valid=True, build_indexes=False)
            coordinates, confidences = (FormattedDataset & key).fetch1(
                "coordinates", "confidences"
            )

            data, metadata = format_data(
                **config, coordinates=coordinates, confidences=confidences
            )

            pca = fit_pca(data, **config)
            pca_path = os.path.join(
                output_dir, "pca_{}.p".format(key["pca_fitting_id"])
            )
            save_pca(pca, pca_path)  # The model is saved
            creation_time = datetime.utcnow()
        else:
            creation_time = None

        self.insert1(**key, pca_fitting_time=creation_time)

    
@schema
class DimsExplainedVariance(dj.Computed):
    """
    This is an optional table to compute and store the latent dimensions that explain a certain specified variance threshold.
    """
    definition = """
    -> PCAFitting
    variance_threshold : float                 # Variance threshold to be explained by the PCA model
    ---
    variance_percentage     : float 
    dims_explained_variance : int
    latent_dim_desc: varchar(1000)
    """
    
    def make(self, key):
        variance_threshold, output_dir = (PCATask & key).fetch1(
            "variance_threshold", "output_dir"
        )
        pca = load_pca(output_dir)
        cs = np.cumsum(pca.explained_variance_ratio_)
        # explained_variance_ratio_ndarray of shape (n_components,)
        # Percentage of variance explained by each of the selected components.
        # If n_components is not set then all components are stored and the sum of the ratios is equal to 1.0.
        if cs[-1] < variance_threshold: 
            dims_explained_variance = len(cs)
            variance_percentage = cs[-1]*100
            latent_dim_description= f"All components together only explain {cs[-1]*100}% of variance."
        else:
            dims_explained_variance = (cs>variance_threshold).nonzero()[0].min()+1
            variance_percentage = variance_threshold*100
            latent_dim_description= f">={variance_threshold*100}% of variance exlained by {(cs>variance_threshold).nonzero()[0].min()+1} components."
        
        self.insert1(dict(**key, 
                          variance_percentage = variance_percentage,
                          dims_explained_variance=dims_explained_variance,
                          latent_dim_description=latent_dim_description))
