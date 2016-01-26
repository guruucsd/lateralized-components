# *- encoding: utf-8 -*-
# Author: Ben Cipollini, Ami Tsuchida
# License: BSD

import os.path as op

import nibabel as nib
import numpy as np
import matplotlib.pyplot as plt
from nilearn import datasets
from nilearn.image import index_img, iter_img
from itertools import chain

from nibabel_ext import NiftiImageWithTerms
from nilearn_ext.datasets import fetch_neurovault
from nilearn_ext.decomposition import compare_components, generate_components
from nilearn_ext.masking import join_bilateral_rois
from nilearn_ext.plotting import (plot_component_comparisons, plot_components,
                                  plot_comparison_matrix)


def load_or_generate_components(hemi, out_dir='.', plot_dir=None,
                                *args, **kwargs):
    """ Load an image and return if it exists, otherwise compute via ICA"""

    # Only re-run if image doesn't exist.
    img_path = op.join(out_dir, '%s_ica_components.nii.gz' % hemi)
    if not kwargs.pop('force') and op.exists(img_path):
        img = NiftiImageWithTerms.from_filename(img_path)
        
    else:
        img = generate_components(hemi=hemi, out_dir=out_dir, *args, **kwargs)
        png_dir = op.join(out_dir, 'png')
        plot_components(img, hemi=hemi, out_dir=png_dir)
    return img


def mix_and_match_bilateral_components(**kwargs):
    """Run ICA on R,L; then match up components and
    and concatenate matched components into a full-brain picture.
    """
    raise NotImplementedError("Ben needs to review this RL/LR code!")

    # LR image: do ICA for L, then R, then match up & combine
    # into a set of bilateral images.
    R_img = load_or_generate_components(hemi='R', **kwargs)  # noqa
    L_img = load_or_generate_components(hemi='L', **kwargs)  # noqa

    # Match
    score_mat = compare_components(images=(R_img, L_img),
                                   labels=('R', 'L'))
    most_similar_idx = score_mat.argmin(axis=1)

    # Mix
    terms = R_img.terms.keys()
    term_scores = []
    bilat_imgs = []
    for rci, R_comp_img in enumerate(iter_img(R_img)):
        lci = most_similar_idx[rci]
        L_comp_img = index_img(L_img, lci)  # noqa
        # combine images
        bilat_imgs.append(join_bilateral_rois(R_comp_img, L_comp_img))
        # combine terms
        if terms:
            term_scores.append([(R_img.terms[t][rci] +
                                 L_img.terms[t][lci]) / 2
                                for t in terms])

    # Squash into single image
    img = nib.concat_images(bilat_imgs)
    if terms:
        img.terms = dict(zip(terms, np.asarray(term_scores).T))
    return img


def get_dataset(dataset, fetch_terms=False, max_images=np.inf,
                **kwargs):
    """Retrieve & normalize dataset from nilearn"""
    # Download
    if dataset == 'neurovault':
        
        # Set image filters: The filt_dict contains metadata field for the key 
        # and the desired entry for each field as the value. 
        # Since neurovault metadata are not always filled, it also includes any 
        # images with missing values for the any given field.
        filt_dict = {'modality':'fMRI-BOLD','analysis_level':'group',
                    'is_thresholded':False,'not_mni':False}
        image_filters =()
        for f in filt_dict:
            fxn =  [lambda img: (img.get(f) or '') == '' or img[f] == filt_dict[f]]
            image_filters = chain(image_filters, fxn)  # This part isn't working...:-(
        
        # Also remove bad collections 
        bad_collects = [367,     # Contains a single image with a large area of uniform nonzero value
                       1003,     # All three collections contain stat maps on parcellated
                       1011,     # brains. Inclusion of these images is suspected to 
                       1013]     # result in a weird-looking ICA component
        col_ids = [-bid for bid in bad_collects]
        
        images, term_scores = fetch_neurovault(
            max_images=max_images, fetch_terms=fetch_terms,
            collection_ids=col_ids, image_filters=image_filters, **kwargs)

    elif dataset == 'abide':
        dataset = datasets.fetch_abide_pcp(
            n_subjects=min(94, max_images), **kwargs)
        images = [{'local_path': p} for p in dataset['func_preproc']]
        term_scores = None

    elif dataset == 'nyu':
        dataset = datasets.fetch_nyu_rest(
            n_subjects=min(25, max_images), **kwargs)
        images = [{'local_path': p} for p in dataset['func']]
        term_scores = None

    else:
        raise ValueError("Unknown dataset: %s" % dataset)
    return images, term_scores


def main(dataset, keys=('R', 'L'), n_components=20, max_images=np.inf,
         scoring='l1norm', query_server=True,
         force=False, nii_dir=None, plot_dir=None, random_state=42):
    """Compute components, then run requested comparisons"""

    # Output directories
    nii_dir = nii_dir or op.join('ica_nii', dataset, str(n_components))
    plot_dir = plot_dir or op.join('ica_imgs', dataset,
                                   '%s-%dics' % (scoring, n_components))

    images, term_scores = get_dataset(dataset, max_images=max_images,
                                      query_server=query_server)

    # Analyze images
    imgs = []
    kwargs = dict(images=[im['local_path'] for im in images], n_components=n_components,
                  term_scores=term_scores, out_dir=nii_dir, plot_dir=plot_dir)
    for key in (k.lower() for k in keys):
        print("Running analyses on %s" % key)
        if key in ('rl', 'lr'):
            imgs.append(mix_and_match_bilateral_components(**kwargs))
        else:
            imgs.append(load_or_generate_components(
                hemi=key, force=force, random_state=random_state, **kwargs))

    # Show confusion matrix
    score_mat = compare_components(images=imgs, labels=keys,
                                   scoring=scoring)
    plot_comparison_matrix(score_mat, scoring=scoring, normalize=True,
                           out_dir=plot_dir, keys=keys)

    # Get the requested images
    plot_component_comparisons(images=imgs, labels=keys,
                               score_mat=score_mat, out_dir=plot_dir)

    return imgs, keys, score_mat


if __name__ == '__main__':
    import warnings
    from argparse import ArgumentParser

    # Look for image computation errors
    warnings.simplefilter('ignore', DeprecationWarning)
    warnings.simplefilter('error', RuntimeWarning)  # Detect bad NV images

    # Arg parsing
    hemi_choices = ['R', 'L', 'RL', 'LR', 'both']
    parser = ArgumentParser(description="Run ICA on individual hemispheres, "
                                        "or whole brain, then compare.\n\n"
                                        "R=right-only, L=left-only,\n"
                                        "RL=R,L ICA separate, compare as one\n"
                                        "both=ICA & compare together")
    parser.add_argument('key1', nargs='?', default='R', choices=hemi_choices)
    parser.add_argument('key2', nargs='?', default='L', choices=hemi_choices)
    parser.add_argument('--force', action='store_true', default=False)
    parser.add_argument('--offline', action='store_true', default=False)
    parser.add_argument('--qc', action='store_true', default=False)
    parser.add_argument('--components', nargs='?', type=int, default=20,
                        dest='n_components')
    parser.add_argument('--dataset', nargs='?', default='neurovault',
                        choices=['neurovault', 'abide', 'nyu'])
    parser.add_argument('--seed', nargs='?', type=int, default=42,
                        dest='random_state')
    parser.add_argument('--scoring', nargs='?', default='l1norm',
                        choices=['l1norm', 'l2norm', 'correlation'])
    args = vars(parser.parse_args())

    # Run qc
    query_server = not args.pop('offline')
    if args.pop('qc'):
        from qc import qc_image_data
        qc_image_data(args['dataset'], query_server=query_server)

    # Run main
    keys = args.pop('key1'), args.pop('key2')
    main(keys=keys, query_server=query_server, **args)

    plt.show()
