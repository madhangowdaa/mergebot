import asyncio
import os
import time

from bot import (
    LOGGER,
    AUDIO_EXTENSIONS,
    UPLOAD_AS_DOC,
    UPLOAD_TO_DRIVE,
    VIDEO_EXTENSIONS,
    delete_all,
    formatDB,
    gDict,
    queueDB,
)
from config import Config
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from helpers.display_progress import Progress
from helpers.ffmpeg_helper import MergeAudio, take_screen_shot
from helpers.rclone_upload import rclone_driver, rclone_upload
from helpers.uploader import uploadVideo
from helpers.utils import UserSettings
from PIL import Image
from pyrogram import Client
from pyrogram.errors import MessageNotModified
from pyrogram.errors.exceptions.flood_420 import FloodWait
from pyrogram.errors.rpc_error import UnknownError
from pyrogram.types import CallbackQuery, Message


async def mergeAudio(c: Client, cb: CallbackQuery, new_file_name: str):
    omess = cb.message.reply_to_message
    vid_list = list()
    await cb.message.edit("⭕ Processing...")
    duration = 0
    video_mess = queueDB.get(cb.from_user.id)["videos"][0]
    list_message_ids: list = queueDB.get(cb.from_user.id)["audios"]
    list_message_ids.insert(0, video_mess)
    list_message_ids.sort()
    if list_message_ids is None:
        await cb.answer("Queue Empty", show_alert=True)
        await cb.message.delete(True)
        return
    if not os.path.exists(f"downloads/{str(cb.from_user.id)}/"):
        os.makedirs(f"downloads/{str(cb.from_user.id)}/")
    msgs: list[Message] = await c.get_messages(
        chat_id=cb.from_user.id, message_ids=list_message_ids
    )
    all = len(msgs)
    n = 1
    for i in msgs:
        media = i.video or i.document
        await cb.message.edit(f"📥 Starting Download of ... `{media.file_name}`")
        LOGGER.info(f"📥 Starting Download of ... {media.file_name}")
        currentFileNameExt = media.file_name.rsplit(sep=".")[-1].lower()
        if currentFileNameExt in VIDEO_EXTENSIONS:
            tmpFileName = "vid.mkv"
        elif currentFileNameExt in AUDIO_EXTENSIONS:
            tmpFileName = "audio." + currentFileNameExt
        await asyncio.sleep(5)
        file_dl_path = None
        try:
            c_time = time.time()
            prog = Progress(cb.from_user.id, c, cb.message)
            file_dl_path = await c.download_media(
                message=media,
                file_name=f"downloads/{str(cb.from_user.id)}/{str(i.id)}/{tmpFileName}",
                progress=prog.progress_for_pyrogram,
                progress_args=(f"🚀 Downloading: `{media.file_name}`", c_time, f"\n**Downloading: {n}/{all}**"),
            )
            n += 1
            if gDict[cb.message.chat.id] and cb.message.id in gDict[cb.message.chat.id]:
                return
            await cb.message.edit(f"Downloaded Successfully ... `{media.file_name}`")
            LOGGER.info(f"Downloaded Successfully ... {media.file_name}")
            await asyncio.sleep(5)
        except Exception as downloadErr:
            LOGGER.warning(f"Failed to download Error: {downloadErr}")
            queueDB.get(cb.from_user.id)["audios"].remove(i.id)
            await cb.message.edit("⚠️ Failed to download " + f"**{media.file_name}**")
            continue
        vid_list.append(file_dl_path)
    await MergeAudioNew(vid_list, f"downloads/{str(cb.from_user.id)}/final_{cb.from_user.id}.mkv")
    LOGGER.info("Audio Muxed Successfully ...")
    await cb.message.edit("🔁 Adding Audio to Video ...")
    await asyncio.sleep(5)
    f = f"downloads/{str(cb.from_user.id)}/final_{cb.from_user.id}.mkv"
    if not os.path.exists(f):
        LOGGER.info("Audio muxing Failed ...")
        await cb.message.edit("⚠️ Failed to Add Audio to Video!")
        await asyncio.sleep(2)
        for e in vid_list:
            if os.path.exists(e):
                os.remove(e)
        await delete_all(cb.message.chat.id)
        formatDB.set(cb.message.chat.id, "caption", None)
        return
    LOGGER.info("Audio muxed Successfully ...")
    await cb.message.edit("✅ Successfully Muxed Audio!")
    await asyncio.sleep(2)
    await cb.message.edit("⚙️ Preparing to Upload ...")
    await asyncio.sleep(2)
    if Config.THUMBNAIL and os.path.exists(f"downloads/{str(cb.from_user.id)}/thumb.jpg"):
        thumb = f"downloads/{str(cb.from_user.id)}/thumb.jpg"
    else:
        LOGGER.info("Taking Screenshot to use as thumb...")
        thumb = f"downloads/{str(cb.from_user.id)}/thumb.jpg"
        await take_screen_shot(f, 0, thumb)
        LOGGER.info("Thumbnail Generated Successfully ...")
    if UPLOAD_AS_DOC:
        formatDB.set(cb.message.chat.id, "doc", True)
    elif UPLOAD_TO_DRIVE[cb.from_user.id]:
        formatDB.set(cb.message.chat.id, "drive", True)
    file_size = os.path.getsize(f)
    if file_size >= 1073741824:
        size = f"{file_size / 1073741824:.2f} GB"
    elif file_size >= 1048576:
        size = f"{file_size / 1048576:.2f} MB"
    else:
        size = f"{file_size / 1024:.2f} KB"
    formatDB.set(cb.message.chat.id, "size", size)
    formatDB.set(cb.message.chat.id, "duration", duration)
    await cb.message.edit("🔄 Uploading ...")
    await cb.message.edit("📤 Uploading ...")
    LOGGER.info(f"Uploading: {new_file_name} ...")
    LOGGER.info("Uploading Started ...")
    if formatDB.get(cb.message.chat.id, "drive"):
        try:
            await rclone_upload(c, cb, f, new_file_name)
        except Exception as err:
            LOGGER.info(f"Unable to Upload to Gdrive Error: {err}")
            formatDB.set(cb.message.chat.id, "drive", False)
            formatDB.set(cb.message.chat.id, "doc", True)
            await uploadVideo(c, cb, f, new_file_name, thumb)
    elif formatDB.get(cb.message.chat.id, "doc"):
        await uploadVideo(c, cb, f, new_file_name, thumb)
    LOGGER.info(f"Uploading: {new_file_name} Completed!")
    await cb.message.delete()
    formatDB.set(cb.message.chat.id, "caption", None)
    formatDB.set(cb.message.chat.id, "drive", False)
    formatDB.set(cb.message.chat.id, "doc", False)
    formatDB.set(cb.message.chat.id, "duration", None)
    formatDB.set(cb.message.chat.id, "size", None)
    await delete_all(cb.message.chat.id)
# import asyncio
# import os
# import time

# from bot import (AUDIO_EXTENSIONS, LOGGER, UPLOAD_AS_DOC, UPLOAD_TO_DRIVE,
#                  VIDEO_EXTENSIONS, delete_all, formatDB, gDict, queueDB)
# from config import Config
# from hachoir.metadata import extractMetadata
# from hachoir.parser import createParser
# from helpers.display_progress import Progress
# from helpers.ffmpeg_helper import MergeAudio, take_screen_shot
# from helpers.rclone_upload import rclone_driver, rclone_upload
# from helpers.uploader import uploadVideo
# from helpers.utils import UserSettings
# from PIL import Image
# from pyrogram import Client
# from pyrogram.errors import MessageNotModified
# from pyrogram.types import CallbackQuery, Message


# async def mergeAudio(c: Client, cb: CallbackQuery, new_file_name: str):
#     omess = cb.message.reply_to_message
#     files_list = []
#     await cb.message.edit("⭕ Processing...")
#     duration = 0
#     video_mess = queueDB.get(cb.from_user.id)["videos"][0]
#     list_message_ids: list = queueDB.get(cb.from_user.id)["audios"]
#     list_message_ids.insert(0, video_mess)
#     list_message_ids.sort()
#     if list_message_ids is None:
#         await cb.answer("Queue Empty", show_alert=True)
#         await cb.message.delete(True)
#         return
#     if not os.path.exists(f"downloads/{str(cb.from_user.id)}/"):
#         os.makedirs(f"downloads/{str(cb.from_user.id)}/")
#     all = len(list_message_ids)
#     n=1
#     msgs: list[Message] = await c.get_messages(
#         chat_id=cb.from_user.id, message_ids=list_message_ids
#     )
#     for i in msgs:
#         media = i.video or i.document or i.audio
#         await cb.message.edit(f"📥 Starting Download of ... `{media.file_name}`")
#         LOGGER.info(f"📥 Starting Download of ... {media.file_name}")
#         currentFileNameExt = media.file_name.rsplit(sep=".")[-1].lower()
#         if currentFileNameExt in VIDEO_EXTENSIONS:
#             tmpFileName = "vid.mkv"
#         elif currentFileNameExt in AUDIO_EXTENSIONS:
#             tmpFileName = "audio." + currentFileNameExt
#         await asyncio.sleep(5)
#         file_dl_path = None
#         try:
#             c_time = time.time()
#             prog = Progress(cb.from_user.id, c, cb.message)
#             file_dl_path = await c.download_media(
#                 message=media,
#                 file_name=f"downloads/{str(cb.from_user.id)}/{str(i.id)}/{tmpFileName}",
#                 progress=prog.progress_for_pyrogram,
#                 progress_args=(f"🚀 Downloading: `{media.file_name}`", c_time, f"\n**Downloading: {n}/{all}**"),
#             )
#             n+=1
#             if gDict[cb.message.chat.id] and cb.message.id in gDict[cb.message.chat.id]:
#                 return
#             await cb.message.edit(f"Downloaded Sucessfully ... `{media.file_name}`")
#             LOGGER.info(f"Downloaded Sucessfully ... {media.file_name}")
#             await asyncio.sleep(4)
#         except Exception as downloadErr:
#             LOGGER.warning(f"Failed to download Error: {downloadErr}")
#             queueDB.get(cb.from_user.id)["audios"].remove(i.id)
#             await cb.message.edit("❗File Skipped!")
#             await asyncio.sleep(4)
#             await cb.message.delete(True)
#             continue
#         files_list.append(f"{file_dl_path}")

#     muxed_video = MergeAudio(files_list[0], files_list, cb.from_user.id)
#     if muxed_video is None:
#         await cb.message.edit("❌ Failed to add audio to video !")
#         await delete_all(root=f"downloads/{str(cb.from_user.id)}")
#         queueDB.update({cb.from_user.id: {"videos": [], "subtitles": [], "audios": []}})
#         formatDB.update({cb.from_user.id: None})
#         return
#     try:
#         await cb.message.edit("✅ Sucessfully Muxed Video !")
#     except MessageNotModified:
#         await cb.message.edit("Sucessfully Muxed Video ! ✅")
#     LOGGER.info(f"Video muxed for: {cb.from_user.first_name} ")
#     await asyncio.sleep(3)
#     file_size = os.path.getsize(muxed_video)
#     os.rename(muxed_video, new_file_name)
#     await cb.message.edit(
#         f"🔄 Renaming Video to\n **{new_file_name.rsplit('/',1)[-1]}**"
#     )
#     await asyncio.sleep(4)
#     merged_video_path = new_file_name

#     if UPLOAD_TO_DRIVE[f"{cb.from_user.id}"]:
#         # uploads to drive using rclone
#         await rclone_driver(omess, cb, merged_video_path)
#         await delete_all(root=f"downloads/{str(cb.from_user.id)}")
#         queueDB.update({cb.from_user.id: {"videos": [], "subtitles": [], "audios": []}})
#         formatDB.update({cb.from_user.id: None})
#         return

#     if file_size > 2044723200 and Config.IS_PREMIUM == False:
#         await cb.message.edit(
#             f"Video is Larger than 2GB Can't Upload,\n\n Tell {Config.OWNER_USERNAME} to add premium account to get 4GB TG uploads"
#         )
#         await delete_all(root=f"downloads/{str(cb.from_user.id)}")
#         queueDB.update({cb.from_user.id: {"videos": [], "subtitles": [], "audios": []}})
#         formatDB.update({cb.from_user.id: None})
#         return
#     if Config.IS_PREMIUM and file_size > 4241280205:
#         await cb.message.edit(
#             f"Video is Larger than 4GB Can't Upload,\n\n Tell {Config.OWNER_USERNAME} to die with premium account"
#         )
#         await delete_all(root=f"downloads/{str(cb.from_user.id)}")
#         queueDB.update({cb.from_user.id: {"videos": [], "subtitles": [], "audios": []}})
#         formatDB.update({cb.from_user.id: None})
#         return
#     await cb.message.edit("🎥 Extracting Video Data ...")

#     duration = 1
#     try:
#         metadata = extractMetadata(createParser(merged_video_path))
#         if metadata.has("duration"):
#             duration = metadata.get("duration").seconds
#     except Exception as er:
#         await delete_all(root=f"downloads/{str(cb.from_user.id)}")
#         queueDB.update({cb.from_user.id: {"videos": [], "subtitles": [], "audios": []}})
#         formatDB.update({cb.from_user.id: None})
#         await cb.message.edit("⭕ Merged Video is corrupted")
#         return
#     try:
#         user = UserSettings(cb.from_user.id, cb.from_user.first_name)
#         thumb_id = user.thumbnail
#         if thumb_id is None:
#             raise Exception
#         video_thumbnail = f"downloads/{str(cb.from_user.id)}_thumb.jpg"
#         await c.download_media(message=str(thumb_id), file_name=video_thumbnail)
#     except Exception as err:
#         LOGGER.info("Generating thumb")
#         video_thumbnail = await take_screen_shot(
#             merged_video_path, f"downloads/{str(cb.from_user.id)}", (duration / 2)
#         )
#     width = 1280
#     height = 720
#     try:
#         thumb = extractMetadata(createParser(video_thumbnail))
#         height = thumb.get("height")
#         width = thumb.get("width")
#         img = Image.open(video_thumbnail)
#         if width > height:
#             img.resize((320, height))
#         elif height > width:
#             img.resize((width, 320))
#         img.save(video_thumbnail)
#         Image.open(video_thumbnail).convert("RGB").save(video_thumbnail, "JPEG")
#     except:
#         await delete_all(root=f"downloads/{str(cb.from_user.id)}")
#         queueDB.update({cb.from_user.id: {"videos": [], "subtitles": [], "audios": []}})
#         formatDB.update({cb.from_user.id: None})
#         await cb.message.edit(
#             "⭕ Merged Video is corrupted \n\n<i>Try setting custom thumbnail</i>",
#         )
#         return
#     await uploadVideo(
#         c=c,
#         cb=cb,
#         merged_video_path=merged_video_path,
#         width=width,
#         height=height,
#         duration=duration,
#         video_thumbnail=video_thumbnail,
#         file_size=os.path.getsize(merged_video_path),
#         upload_mode=UPLOAD_AS_DOC[f"{cb.from_user.id}"],
#     )
#     await cb.message.delete(True)
#     await delete_all(root=f"downloads/{str(cb.from_user.id)}")
#     queueDB.update({cb.from_user.id: {"videos": [], "subtitles": [], "audios": []}})
#     formatDB.update({cb.from_user.id: None})
#     return
