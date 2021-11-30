from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import shutil

import torch
import torch.nn.functional as F
import torch.nn as nn
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
import pickle
import glob
import random
import cv2
from scipy import ndimage
import os

from libs import helpers,utils_torch
from davisinteractive.utils.visualization import *
from davisinteractive.utils.operations import bresenham
from scipy.ndimage.morphology import distance_transform_edt

from PIL import Image


def mkdir(paths):
    if not isinstance(paths, (list, tuple)):
        paths = [paths]
    for path in paths:
        if not os.path.isdir(path):
            os.makedirs(path)


def apply_pad(img, padinfo=None):
    if padinfo: # ((hpad,hpad),(wpad,wpad))
        (hpad, wpad) = padinfo
        if len(img.shape)==3 :  pad_img = np.pad(img, (hpad, wpad, (0, 0)), mode='reflect') # H,W,3
        else: pad_img = np.pad(img, (hpad, wpad), mode='reflect') #H,W
        return pad_img
    else:
        h, w = img.shape[0:2]
        new_h = h + 32 - h % 32
        new_w = w + 32 - w % 32
        # print(new_h, new_w)
        lh, uh = (new_h - h) / 2, (new_h - h) / 2 + (new_h - h) % 2
        lw, uw = (new_w - w) / 2, (new_w - w) / 2 + (new_w - w) % 2
        lh, uh, lw, uw = int(lh), int(uh), int(lw), int(uw)
        if len(img.shape)==3 :  pad_img = np.pad(img, ((lh, uh), (lw, uw), (0, 0)), mode='reflect') # H,W,3
        else: pad_img = np.pad(img, ((lh, uh), (lw, uw)), mode='reflect') # H,W
        info = ((lh, uh), (lw, uw))

        return pad_img, info


def _pascal_color_map(N=256, normalized=True):
    """
    Python implementation of the color map function for the PASCAL VOC data set.
    Official Matlab version can be found in the PASCAL VOC devkit
    http://host.robots.ox.ac.uk/pascal/VOC/voc2012/index.html#devkit
    """

    def bitget(byteval, idx):
        return (byteval & (1 << idx)) != 0

    dtype = 'float32' if normalized else 'uint8'
    cmap = np.zeros((N, 3), dtype=dtype)
    for i in range(N):
        r = g = b = 0
        c = i
        for j in range(8):
            r = r | (bitget(c, 0) << 7 - j)
            g = g | (bitget(c, 1) << 7 - j)
            b = b | (bitget(c, 2) << 7 - j)
            c = c >> 3

        cmap[i] = np.array([r, g, b])

    cmap = cmap / 255 if normalized else cmap
    return cmap


def get_prop_list(annotated_frames, annotated_now, num_frames, proportion = 1.0, get_close_anno_frames = False):

    aligned_anno = sorted(annotated_frames)
    overlap = aligned_anno.count(annotated_now)
    for i in range(overlap):
        aligned_anno.remove(annotated_now)

    start_frame, end_frame = 0, num_frames -1
    for i in range(len(aligned_anno)):
        if aligned_anno[i] > annotated_now:
            end_frame = aligned_anno[i] - 1
            break
    aligned_anno.reverse()
    for i in range(len(aligned_anno)):
        if aligned_anno[i] < annotated_now:
            start_frame = aligned_anno[i]+1
            break

    if get_close_anno_frames:
        close_frames_round=dict() # 1st column: iaction idx, 2nd column: the close frames
        annotated_frames.reverse()
        try: close_frames_round["left"] = len(annotated_frames) - annotated_frames.index(start_frame-1) - 1
        except: print('No left annotated fr')
        try: close_frames_round["right"] = len(annotated_frames) - annotated_frames.index(end_frame) - 1
        except: print('No right annotated fr')

    if proportion != 1.0:
        if start_frame!=0:
            start_frame = annotated_now - int((annotated_now-start_frame)*proportion + 0.5)
        if end_frame != num_frames-1:
            end_frame = annotated_now + int((end_frame - annotated_now) * proportion + 0.5)
    prop_list = list(range(annotated_now,start_frame-1,-1)) + list(range(annotated_now,end_frame+1))
    if len(prop_list)==0:
        prop_list = [annotated_now]

    if not get_close_anno_frames:
        return prop_list

    else:
        return prop_list, close_frames_round

def scribble_to_image(scribbles, currentframe, obj_id, prev_mask, dilation=5,
                      nocare_area=None, bresenhamtf=True, blur=True, singleimg=False, seperate_pos_neg = False):
    """ Make scrible to previous mask shaped numpyfile

    """
    h,w = prev_mask.shape
    regions2exclude_on_maskneg = prev_mask!=obj_id
    mask = np.zeros([h,w])
    mask_neg = np.zeros([h,w])
    if singleimg:
        scribbles=scribbles
    else: scribbles = scribbles[currentframe]

    for scribble in scribbles:
        points_scribble = np.round(np.array(scribble['path']) * np.array((w, h))).astype(np.int)
        if bresenhamtf and len(points_scribble) > 1:
            all_points = bresenham(points_scribble)
        else:
            all_points = points_scribble

        if obj_id==0:
            raise NotImplementedError
        else:
            if scribble['object_id'] == obj_id:
                mask[all_points[:, 1] - 1, all_points[:, 0] - 1] = 1
            else:
                mask_neg[all_points[:, 1] - 1, all_points[:, 0] - 1] = 1
        # else:
        #     mask_neg[all_points[:, 1] - 1, all_points[:, 0] - 1] = 1

    scr_gt, _ = helpers.scrimg_postprocess(mask, dilation=dilation, nocare_area=nocare_area, blur=blur, blursize=(5, 5))
    scr_gt_neg, _ = helpers.scrimg_postprocess(mask_neg, dilation=dilation, nocare_area=nocare_area, blur=blur, blursize=(5, 5))
    scr_gt_neg[regions2exclude_on_maskneg] = 0

    if seperate_pos_neg:
        return scr_gt.astype(np.float32), scr_gt_neg.astype(np.float32)
    else:
        scr_img = scr_gt - scr_gt_neg
        return scr_img.astype(np.float32)


class logger:
    def __init__(self, log_file):
        self.log_file = log_file

    def printNlog(self,str2print):
        print(str2print)
        with open(self.log_file, 'a') as f:
            f.write(str2print + '\n')
            f.close()

def printNlog(str2print, log_file):
    print(str2print)
    with open(log_file, 'a') as f:
        f.write(str2print+'\n')
        f.close()


def load_frames(path, size=None, num_frames=None):
    fnames = glob.glob(os.path.join(path, '*.jpg'))
    fnames.sort()
    frame_list = []
    for i, fname in enumerate(fnames):
        if size:
            frame_list.append(np.array(Image.open(fname).convert('RGB').resize((size[0], size[1]), Image.BICUBIC), dtype=np.uint8))
        else:
            frame_list.append(np.array(Image.open(fname).convert('RGB'), dtype=np.uint8))
        if num_frames and i > num_frames:
            break
    frames = np.stack(frame_list, axis=0)
    return frames

def load_gts(path, size=None, num_frames=None):
    fnames = glob.glob(os.path.join(path, '*.png'))
    fnames.sort()
    frame_list = []
    for i, fname in enumerate(fnames):
        if size:
            frame_list.append(np.array(Image.open(fname).resize((size[0], size[1]), Image.BICUBIC), dtype=np.uint8))
        else:
            frame_list.append((np.array(Image.open(fname))).astype(np.bool).astype(np.uint8))
        if num_frames and i > num_frames:
            break
    segs = np.stack(frame_list, axis=0)
    return segs

def load_gts_multi(path, num_frames=None):
    fnames = glob.glob(os.path.join(path, '*.png'))
    fnames.sort()
    frame_list = []
    for i, fname in enumerate(fnames):
        frame_list.append((np.array(Image.open(fname))).astype(np.uint8))
        if num_frames and i > num_frames:
            break
    segs = np.stack(frame_list, axis=0)
    return segs



if __name__ =='__main__':
    get_prop_list([50, 70, 90], 71, 100, 0.67)