#!/usr/bin/env python
# encoding: utf-8

# The MIT License (MIT)

# Copyright (c) 2016-2018 CNRS

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# AUTHORS
# Hervé BREDIN - http://herve.niderb.fr

import warnings
import numpy as np

from .utils import RawAudio
from .utils import read_audio
from .utils import get_audio_duration

from pyannote.core import Segment
from pyannote.core import SlidingWindow
from pyannote.core import SlidingWindowFeature

from pyannote.database import get_unique_identifier

import librosa.util

class FeatureExtraction(object):
    """Base class for feature extraction

    Parameters
    ----------
    augmentation : `pyannote.audio.augmentation.Augmentation`, optional
        Data augmentation.
    normalization : callable, optional
        Feature normalization. See
        `pyannote.audio.features.normalization.ShortTermStandardization` for an
        example.
    sample_rate : int, optional
        Convert audio to use this sample rate.

    See also
    --------
    `pyannote.audio.augmentation.AddNoise`
    `pyannote.audio.features.normalization.ShortTermStandardization`
    """

    def __init__(self, augmentation=None, normalization=None,
                 sample_rate=None):
        super().__init__()
        self.augmentation = augmentation
        self.normalization = normalization
        self.sample_rate = sample_rate

        # used in FeatureExtraction.crop
        self.raw_audio_ = RawAudio(sample_rate=self.sample_rate, mono=True)

    def get_dimension(self):
        """Get dimension of feature vectors

        Returns
        -------
        dimension : int
            Dimension of feature vectors
        """
        msg = ('`FeatureExtraction subclasses must implement '
               '`get_dimension` method.')
        raise NotImplementedError(msg)

    @property
    def dimension(self):
        """Dimension of feature vectors"""
        return self.get_dimension()

    def get_sliding_window(self):
        """Get sliding window used for feature extraction

        Returns
        -------
        sliding_window : `pyannote.core.SlidingWindow`
            Sliding window used for feature extraction.
        """

        msg = ('`FeatureExtraction` subclasses must implement '
               '`get_sliding_window` method.')
        raise NotImplementedError(msg)

    @property
    def sliding_window(self):
        """Sliding window used for feature extraction"""
        return self.get_sliding_window()

    def get_features(self, y, sample_rate):
        """Extract features from waveform

        Parameters
        ----------
        y : (n_samples, 1) numpy array
            Waveform.
        sample_rate : int
            Sample rate.

        Returns
        -------
        features : (n_frames, dimension) numpy array
            Extracted features
        """
        msg = ('`FeatureExtractions subclasses must implement '
               '`get_features` method.')
        raise NotImplementedError(msg)

    def __call__(self, current_file):
        """Extract features from file

        Parameters
        ----------
        current_file : dict
            `pyannote.database` files.

        Returns
        -------
        features : `pyannote.core.SlidingWindowFeature`
            Extracted features
        """

        if 'waveform' in current_file:
            # use `waveform` when provided
            y = current_file['waveform']
            # NOTE: we assume that sample rate is correct
            sample_rate = self.sample_rate

        else:
            # load waveform, re-sample, and convert to mono if needed
            y, sample_rate = read_audio(
                current_file, sample_rate=self.sample_rate, mono=True)

        # on-the-fly data augmentation
        if self.augmentation is not None:
            y = self.augmentation(y, sample_rate)

        features = self.get_features(y, sample_rate)

        # basic quality check
        if np.any(np.isnan(features)):
            uri = get_unique_identifier(current_file)
            msg = f'Features extracted from "{uri}" contain NaNs.'
            warnings.warn(msg.format(uri=uri))

        # wrap features in a SlidingWindowFeature instance
        features = SlidingWindowFeature(features, self.sliding_window)

        # normalization
        if self.normalization is not None:
            features = self.normalization(features)

        # return features as `SlidingWindowFeature` instances
        return features

    def get_context_duration(self):
        """

        TODO. explain why this is needed

        Returns
        -------
        context : float
            Context duration, in seconds.
        """
        return 0.

    def crop(self, current_file, segment, mode='center', fixed=None):
        """Fast version of self(current_file).crop(segment, mode='center',
+                                                  fixed=segment.duration)

        Parameters
        ----------
        current_file : dict
            `pyannote.database` file.
        segment : `pyannote.core.Segment`
            Segment from which to extract features.

        Returns
        -------
        features : (n_frames, dimension) numpy array
            Extracted features

        See also
        --------
        `pyannote.core.SlidingWindowFeature.crop`
        """

        if 'waveform' in current_file:
            y = current_file['waveform']
            duration = len(y) / self.sample_rate
        else:
            duration = get_audio_duration(current_file)

        context = self.get_context_duration()
        if self.normalization:
            context += self.normalization.get_context_duration()

        # extend segment on both sides with requested context
        xsegment = Segment(max(0, segment.start - context),
                           min(duration, segment.end + context))

        # obtain waveform on this extended segment
        y = self.raw_audio_.crop(current_file, xsegment)

        try:
            valid = librosa.util.valid_audio(y, mono=True)
        except ValueError as e:
            msg = (f"Something went wrong when trying to extract waveform of "
                   f"file {current_file['uri']} between {xsegment.start:.3f}s "
                   f"and {xsegment.end:.3f}s.")
            raise ValueError(msg)

        # data augmentation
        if self.augmentation is not None:
            y = self.augmentation(y, self.sample_rate)

        features = self.get_features(y, self.sample_rate)

        # normalization
        if self.normalization:
            features = self.normalization(features,
                                          sliding_window=self.sliding_window)

        # get rid of additional context before returning
        frames = self.sliding_window
        shifted_frames = SlidingWindow(start=xsegment.start - frames.step,
                                       step=frames.step,
                                       duration=frames.duration)
        (start, end), = shifted_frames.crop(segment, mode=mode, fixed=fixed,
                                            return_ranges=True)
        return features[start:end]
