"""
This module contains the Layer Mode widget

TODO:
* input:
    * checkbox to segment only on FoV
* run button disabled if no image selected
* cancel button: stop `cellseg_predict` -> necessary?
* output has estimate of segmentation quality (GA's suggestion)
    * input: bf + nuclear fluorescence from layer list
    * run prediction with existing model
    * tile input image and ask user to segment cells in these tiles
    * with GT mask (= IoU), without mask (predict IoU based on mask features)

"""

import os
from typing import TYPE_CHECKING, Union

from napari.utils.notifications import show_info

try:
    import torch
except ImportError:
    show_info("Please wait while PyTorch dependencies are being installed")
    os.system(
        "ltt install torch==1.10.2 torchvision==0.11.3"
        " pytorch-lightning==1.5.9"
    )
    import torch

from magicgui import magicgui

from napari_cellseg._utils import _postprocess, _preprocess, change_handler

if TYPE_CHECKING:
    import napari

model_type_choices = [
    ("NucSeg", "nucleus_segmentation"),
    ("CellSeg", "cell_segmentation"),
    ("Custom", "custom model"),
]

models_reg = {
    "model_nucseg": [("Bright Field", "bright_field")],
    "model_cellseg": [("Bright Field", "bright_field")],
}

device_choices = [("CPU", "cpu")]

if torch.cuda.is_available():
    device_choices += [
        (f"GPU {i} ({torch.cuda.get_device_name(i)})", f"cuda:{i}")
        for i in range(torch.cuda.device_count())
    ]

# -----------------------------------------------------------------------------

DEBUG = False

DEFAULTS = {
    "model_type": model_type_choices[0][1],
    "model_nucseg": models_reg["model_nucseg"][0][1],
    "model_cellseg": models_reg["model_cellseg"][0][1],
    "device": device_choices[0][1],
    "instance_segmentation": False,
    "remove_holes": 1,
    "size_min": 1,
    "size_max": 1000000,
    "dilation": 0,
}

TOOLTIPS = {
    "img": "Select layer you want to segment (ONLY images).",
    "model_nucseg": "Select mode of pretrained model.",
    "model_cellseg": "Select mode of pretrained model.",
    "model_custom": "Select path to custom model.",
    "device": "Computing device used for predictions.",
    "instance_segmentation": "Check to enable instance segmetation.",
    "remove_holes": "Remove holes in objects of size < X [px].",
    "size_min": "Remove objects of size < X [px] prior to dilation.",
    "size_max": "Remove objects of size > X [px] prior to dilation.",
    "dilation": "Dilate (>0) or shrink (<0) mask by X [px].",
}

# -----------------------------------------------------------------------------


def make_layer_mode_widget():
    @magicgui(
        img=dict(
            label="Input Image",
            tooltip=TOOLTIPS["img"],
        ),
        label_nn=dict(
            widget_type="Label", label="<br><b>Neural Network Selection:</b>"
        ),
        model_type=dict(
            widget_type="RadioButtons",
            label="Model Type",
            orientation="horizontal",
            choices=model_type_choices,
            value=DEFAULTS["model_type"],
        ),
        model_nucseg=dict(
            widget_type="ComboBox",
            visible=False,
            label="Pre-trained Model",
            tooltip=TOOLTIPS["model_nucseg"],
            choices=models_reg["model_nucseg"],
            value=DEFAULTS["model_nucseg"],
        ),
        model_cellseg=dict(
            widget_type="ComboBox",
            visible=False,
            label="Pre-trained Model",
            tooltip=TOOLTIPS["model_cellseg"],
            choices=models_reg["model_cellseg"],
            value=DEFAULTS["model_cellseg"],
        ),
        model_custom=dict(
            widget_type="FileEdit",
            visible=False,
            label="Custom Model",
            tooltip=TOOLTIPS["model_custom"],
            mode="r",
        ),
        device=dict(
            widget_type="ComboBox",
            visible=True,
            label="Computing Device",
            tooltip=TOOLTIPS["device"],
            choices=device_choices,
            value=DEFAULTS["device"],
        ),
        label_postprocessing=dict(
            widget_type="Label", label="<br><b>Post-processing:</b>"
        ),
        instance_segmentation=dict(
            widget_type="CheckBox",
            label="Instance Segmentation",
            tooltip=TOOLTIPS["instance_segmentation"],
            value=DEFAULTS["instance_segmentation"],
        ),
        remove_holes=dict(
            widget_type="SpinBox",
            label="Remove Holes <",
            tooltip=TOOLTIPS["remove_holes"],
            value=DEFAULTS["remove_holes"],
            min=1,
            max=1000000,
            step=1,
        ),
        size_min=dict(
            widget_type="SpinBox",
            label="Minimum object size",
            tooltip=TOOLTIPS["size_min"],
            value=DEFAULTS["size_min"],
            min=1,
            max=1000000,
            step=1,
        ),
        size_max=dict(
            widget_type="SpinBox",
            label="Maximum object size",
            tooltip=TOOLTIPS["size_max"],
            value=DEFAULTS["size_max"],
            min=0,
            max=1000000,
            step=1,
        ),
        dilate=dict(
            widget_type="SpinBox",
            label="Dilation",
            tooltip=TOOLTIPS["dilation"],
            value=DEFAULTS["dilation"],
            min=-1000,
            max=1000,
            step=1,
        ),
    )
    def layer_mode_widget(
        viewer: "napari.viewer.Viewer",
        img: "napari.layers.Image",
        label_nn: str,
        model_type: str,
        model_nucseg: str,
        model_cellseg: str,
        model_custom: str,
        device: str,
        label_postprocessing: str,
        instance_segmentation: bool,
        remove_holes: int,
        size_min: int,
        size_max: int,
        dilate: int,
    ) -> None:
        # import packages at run time
        import pooch
        import torch
        from cellseg.models.unet import LitUnet

        # convert device to torch.device
        device = torch.device(device)

        # Fetch models
        # TODO: add versions for models
        # TODO: change location of models
        # TODO: does not work with GitHub actions
        THATCHER = pooch.create(
            path=pooch.os_cache("napari_cellseg"),
            base_url="https://polybox.ethz.ch/remote.php/webdav/unet_models/",
            registry={
                "cellseg_model.ckpt": (
                    "f76cb3d478a44e1ae80170f8b7b64911ff348"
                    "eba03c040876829fbf4ef4f422c"
                ),
                "nucseg_model.ckpt": (
                    "7e302470af7e2aba5bd456082a6185aa73417e"
                    "ff49330554f9cd6382264f9b1f"
                ),
            },
        )
        polybox_acc = os.environ.get("POLYBOX_ACC")
        polybox_pw = os.environ.get("POLYBOX_PW")
        download_auth = pooch.HTTPDownloader(auth=(polybox_acc, polybox_pw))

        # update model_type
        if model_type == "nucleus_segmentation":
            path_model = THATCHER.fetch(
                "nucseg_model.ckpt", downloader=download_auth
            )
        elif model_type == "cell_segmentation":
            path_model = THATCHER.fetch(
                "cellseg_model.ckpt", downloader=download_auth
            )
        elif model_type == "custom model":
            path_model = model_custom

        img_ar = img.data

        img_t = _preprocess(img_ar, device=device)

        # load model checkpoint for prediction
        model = LitUnet.load_from_checkpoint(path_model)
        model = model.to(device)

        model.eval()

        # obtain mask
        with torch.no_grad():
            mask = model(img_t)

        mask[mask < 0.5] = 0
        mask[mask >= 0.5] = 1

        # convert prediction to numpy array and plot in layers mode
        mask = (
            mask.mul(255)
            .add_(0.5)
            .clamp_(0, 255)
            .to("cpu", torch.uint8)
            .numpy()[0, 0, :, :]
        )

        if model_type == "nucleus_segmentation":
            mask_name = "mask_nucleus"
        elif model_type == "cell_segmentation":
            mask_name = "mask_cell"
        else:
            mask_name = "mask"

        mask = _postprocess(
            mask=mask,
            remove_holes=remove_holes,
            size_min=size_min,
            size_max=size_max,
            instance_segmentation=instance_segmentation,
            dilate=dilate,
        )

        viewer.add_labels(mask, name=mask_name, opacity=0.7)

    # widgets for model types
    widget_for_modeltype = {
        "nucleus_segmentation": layer_mode_widget.model_nucseg,
        "cell_segmentation": layer_mode_widget.model_cellseg,
        "custom model": layer_mode_widget.model_custom,
    }

    # RadioButtons widget triggers a change event initially
    @change_handler(layer_mode_widget.model_type, init=False, debug=DEBUG)
    def _model_type_change(model_type: Union[str, type]):
        selected = widget_for_modeltype[model_type]
        for w in {
            layer_mode_widget.model_nucseg,
            layer_mode_widget.model_cellseg,
            layer_mode_widget.model_custom,
        } - {selected}:
            w.hide()
        selected.show()

        # trigger _model_change
        selected.changed(selected.value)

    return layer_mode_widget
