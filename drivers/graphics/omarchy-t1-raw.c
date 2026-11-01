// SPDX-License-Identifier: GPL-2.0
/* Raw-only binding for the T1 configuration-2 digitizer; no other HID interfaces. */
#include <linux/hid.h>
#include <linux/module.h>
#include <linux/usb.h>

static bool t1_match(struct hid_device *hdev, bool ignore_special_driver)
{
    struct usb_interface *intf;
    struct usb_device *udev;
    (void)ignore_special_driver;
    if (hdev->bus != BUS_USB || hdev->vendor != 0x05ac || hdev->product != 0x8600 || !hdev->dev.parent)
        return false;
    intf = to_usb_interface(hdev->dev.parent);
    udev = interface_to_usbdev(intf);
    return udev->actconfig && udev->actconfig->desc.bConfigurationValue == 2 &&
           intf->cur_altsetting->desc.bInterfaceNumber == 2;
}

static int t1_probe(struct hid_device *hdev, const struct hid_device_id *id)
{
    int ret;
    (void)id;
    if (!t1_match(hdev, false)) return -ENODEV;
    ret = hid_parse(hdev);
    if (ret) return ret;
    if (hdev->rsize < 6 || memcmp(hdev->rdesc, "\x05\x0d\x09\x05\xa1\x01", 6))
        return -ENODEV;
    ret = hid_hw_start(hdev, HID_CONNECT_HIDRAW);
    if (ret) return ret;
    if (hdev->claimed != HID_CLAIMED_HIDRAW) {
        hid_hw_stop(hdev);
        return -ENODEV;
    }
    hid_info(hdev, "T1 digitizer exposed through hidraw only\n");
    return 0;
}

static void t1_remove(struct hid_device *hdev) { hid_hw_stop(hdev); }
static const struct hid_device_id t1_ids[] = {
    { HID_USB_DEVICE(0x05ac, 0x8600) }, { }
};
MODULE_DEVICE_TABLE(hid, t1_ids);
static struct hid_driver t1_driver = {
    .name = "omarchy-t1-digitizer", .id_table = t1_ids,
    .match = t1_match, .probe = t1_probe, .remove = t1_remove,
};
module_hid_driver(t1_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Raw-only Apple T1 Touch Bar digitizer for a bounded workspace-selector trial");
