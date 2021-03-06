"""
class for manipulating objects
"""
from . import utils
from .exception import *
import prox_oss2 as oss2
import os
import binascii
import base64


VERSION = '1.0.0'


class OssFileManager:
    """
    class for managing oss files
    """

    # since bucket.upload() may get 407 when content is empty,
    # I give all directory objects "$DIR$" as content
    LOCAL_DIR_CONTENT = "$DIR$"
    # MD5 of DIR_CONTENT, local folders will get this MD5 as etag, but md5 of a dir will never be used
    LOCAL_DIR_CONTENT_MD5 = utils.content_md5(LOCAL_DIR_CONTENT)

    MD5_HEADER_STRING = 'Content-MD5'

    BUCKET_ACL_PUBLIC_READ = oss2.BUCKET_ACL_PUBLIC_READ
    BUCKET_ACL_PUBLIC_READ_WRITE = oss2.BUCKET_ACL_PUBLIC_READ_WRITE
    BUCKET_ACL_PRIVATE = oss2.BUCKET_ACL_PRIVATE

    SEP = '/'

    def __init__(self, auth_key, auth_key_secret, endpoint, bucket_name, proxies=None):
        self.__proxies = proxies
        self.__auth = oss2.Auth(auth_key, auth_key_secret)
        self.__service = oss2.Service(self.__auth, endpoint)
        self.__bucket = oss2.Bucket(self.__auth, endpoint, bucket_name, enable_crc=False, proxies=proxies)

    @property
    def bucket_name(self):
        return self.__bucket.bucket_name

    def list_bucket(self):
        """
        list all bucket names under current auth_key, auth_key_secret and endpoint
        :return: bucket name iterator
        """
        try:
            return (bkt.bucket_name for bkt in oss2.BucketIterator(self.__service))
        except Exception as e:
            raise YuiListBucketException(e)

    def change_bucket(self, name):
        """
        change current bucket to the given-named bucket, target bucket must be created first
        :param name:
        :return:
        """
        try:
            tgt_bkt = oss2.Bucket(self.__auth, self.__service.endpoint, name, enable_crc=False, proxies=self.__proxies)
            if tgt_bkt not in self.list_bucket():
                raise YuiChangeBucketException("target bucket does not exist, you may need to create it first")
            self.__bucket = tgt_bkt
        except Exception as e:
            raise YuiChangeBucketException(e)

    def create_bucket(self, name, acl=BUCKET_ACL_PRIVATE, stay=False):
        """
        create new bucket and change current bucket to it
        :param name: bucket name, refer to oss2 docs for the naming rules
        :param acl: bucket acl, default to oss2.BUCKET_ACL_PRIVATE
        :param stay: if True, current bucket will not change to newly created bucket, default to False
        :return:
        """
        try:
            new_bkt = oss2.Bucket(self.__auth, self.__service.endpoint, name, enable_crc=False, proxies=self.__proxies)
            new_bkt.create_bucket(acl)
            if not stay:
                self.__bucket = new_bkt
        except Exception as e:
            raise YuiBucketException(e)

    def delete_bucket(self, name):
        """
        delete a bucket, it must be empty
        :param name:
        :return:
        """
        try:
            if name == self.__bucket.bucket_name:
                raise YuiDeleteBucketException("target bucket can not be the current bucket")
            tgt_bkt = oss2.Bucket(self.__auth, self.__service.endpoint, name)
            tgt_bkt.delete_bucket()
        except oss2.exceptions.BucketNotEmpty:
            raise YuiDeleteBucketException("target bucket is not empty, can not be deleted")
        except oss2.exceptions.NoSuchBucket:
            raise YuiDeleteBucketException("target bucket does not exist")
        except Exception as e:
            raise YuiDeleteBucketException(e)

    def get_md5(self, remote):
        """
        try get file md5 from header 'Content-MD5',
        if failed return etag
        :param remote: abs oss path, directory should end with '/'
        :return: md5 string
        """
        try:
            remote = self.norm_path(remote)
            head = self.__bucket.head_object(remote)
            if self.MD5_HEADER_STRING in head.headers:
                return self.base64_to_md5(head.headers[self.MD5_HEADER_STRING])
            else:
                return head.etag
        except Exception as e:
            raise YuiGetMD5Exception(e)

    def is_exist(self, remote):
        """
        wrapper for Bucket.object_exists()
        :param remote: abs oss path, directory should end with '/'
        :return: boolean
        """
        try:
            remote = self.norm_path(remote)
            return self.__bucket.object_exists(remote)
        except Exception as e:
            raise YuiIsExistException(e)

    def upload(self, local, remote, recursive=False, on_success=None, on_error=None, progress_callback=None):
        """
        upload a file/directory to OSS
        if `local` is a directory and `recursive` set to True, all contents will be uploaded recursively
        if http status of upload result >= 400, `on_error` callback will be called, else `on_success` will be called
        `local`, `remote` and upload result object will be passed to callback methods
        :param local: local source path
        :param remote: abs oss path, directory should end with '/'
        :param recursive: boolean
        :param on_success: success callback
        :param on_error: error callback
        :param progress_callback:
        :return:
        """
        # TODO: resumable support
        # TODO: multipart support
        try:
            local = os.path.abspath(local)
            remote = self.norm_path(remote)
            dest_remote = remote + os.path.split(local)[-1] if self.is_dir(remote) else remote
            dest_remote += self.SEP if os.path.isdir(local) else ''
            if os.path.isdir(local):
                if not self.is_dir(dest_remote):
                    raise YuiUploadException("remote path should be a directory")
                md5_b64 = self.md5_to_base64(self.LOCAL_DIR_CONTENT_MD5)
                result = self.__bucket.put_object(dest_remote, self.LOCAL_DIR_CONTENT,
                                                  headers={self.MD5_HEADER_STRING: md5_b64},
                                                  progress_callback=progress_callback)
                if recursive:
                    for subdir in os.listdir(local):
                        self.upload(os.path.join(local, subdir), dest_remote,
                                    on_success=on_success, on_error=on_error,
                                    recursive=True, progress_callback=progress_callback)
            else:
                md5_b64 = self.md5_to_base64(utils.file_md5(local))
                result = self.__bucket.put_object_from_file(dest_remote, local,
                                                            headers={self.MD5_HEADER_STRING: md5_b64},
                                                            progress_callback=progress_callback)
            if result.status >= 400:
                on_error("upload", local, remote, result) if on_error else None
            else:
                # print("object put | \"" + local + "\" --> \"" + remote + "\"")
                on_success("upload", local, remote, result) if on_success else None
        except Exception as e:
            raise YuiUploadException(e)

    def download(self, remote, local, recursive=False, on_success=None, on_error=None, progress_callback=None):
        """
        download a file
        :param remote:
        :param local:
        :param recursive:
        :param on_success:
        :param on_error:
        :param progress_callback:
        :return:
        """
        # TODO: resumable support
        # TODO: multipart support
        def download_single(rem, loc):
            dest_loc = loc + os.sep + rem.strip(self.SEP).split(self.SEP)[-1] if os.path.isdir(loc) else loc
            dest_loc += os.sep if self.is_dir(rem) else ''
            if self.is_dir(rem):
                os.mkdir(dest_loc)
                res = "mkdir"
            else:
                res = self.__bucket.get_object_to_file(rem, dest_loc,
                                                       progress_callback=progress_callback)
            if res != "mkdir" and res.status >= 400:
                on_error("download", rem, loc, res) if on_error else None
            else:
                # print("object got | \"" + rem + "\" --> \"" + loc + "\"")
                on_success("download", rem, loc, res) if on_success else None

        try:
            local = os.path.abspath(local)
            remote = self.norm_path(remote)
            download_single(remote, local)

            if self.is_dir(remote) and recursive:
                for subdir in self.list_dir(remote, True):
                    postfix = self.SEP.join(subdir.key.strip(self.SEP).split(self.SEP)[:-1])
                    dest_local = os.path.normpath(self.SEP.join([local, postfix]))
                    download_single(subdir.key, dest_local)

        except Exception as e:
            raise YuiDownloadException(e)

    def delete(self, remote, recursive=False, on_success=None, on_error=None):
        """
        delete a file
        :param remote:
        :param recursive:
        :param on_success:
        :param on_error:
        :return: class:`RequestResult <oss2.models.RequestResult>`
        """
        def delete_single(rem):
            result = self.__bucket.delete_object(rem)
            if result.status >= 400:
                on_error("delete", rem, None, result) if on_error else None
            else:
                # print("object deleted | \"" + rem + "\"")
                on_success("delete", rem, None, result) if on_success else None
        try:
            remote = self.norm_path(remote)
            if self.is_dir(remote):
                if recursive:
                    subdirs = list(self.list_dir(remote, True))
                    subdirs.reverse()
                    for subdir in subdirs:
                        delete_single(subdir.key)
                else:
                    raise YuiDeleteException("The directory to be deleted is not empty!")
            else:
                delete_single(remote)
        except Exception as e:
            raise YuiDeleteException(e)

    def copy(self, remote_src, remote_dest, on_success=None, on_error=None):
        """
        copy remote files using Bucket.copy_object()
        :param remote_src:
        :param remote_dest:
        :param on_success:
        :param on_error:
        :return:
        """
        def copy_single(rem_src, rem_dest):
            res = self.__bucket.copy_object(self.__bucket.bucket_name, rem_src, rem_dest)
            if res.status >= 400:
                on_error(rem_src, rem_dest, res) if on_error else None
            else:
                # print("object moved | \"" + rem_src + "\" --> \"" + rem_dest + "\"")
                on_success("copy", rem_src, rem_dest, res) if on_success else None

        try:
            remote_src = self.norm_path(remote_src)
            remote_dest = self.norm_path(remote_dest)
            if self.is_dir(remote_src):
                if not self.is_dir(remote_dest):
                    raise YuiMoveException("destination path should also be a directory")
                if remote_dest.startswith(remote_src):
                    raise YuiCopyException("destination directory is a sub-directory of the source directory")
                prefix = self.SEP.join(remote_src.strip(self.SEP).split(self.SEP)[:-1]) + self.SEP
                for subdir in self.list_dir(remote_src, True):
                    new_path = remote_dest + subdir.key if prefix == self.SEP else subdir.key.replace(prefix, remote_dest)
                    copy_single(subdir.key, new_path)
            else:
                if self.is_dir(remote_dest):
                    prefix = self.SEP.join(remote_src.strip(self.SEP).split(self.SEP)[:-1]) + self.SEP
                    remote_dest = remote_src.replace(prefix, remote_dest)
                copy_single(remote_src, remote_dest)

        except Exception as e:
            raise YuiCopyException(e)

    def move(self, remote_old, remote_new, on_success=None, on_error=None):
        """
        rename a file using Bucket.copy_object() first then delete the original
        :param remote_old:
        :param remote_new:
        :param on_success:
        :param on_error:
        :return:
        """
        def move_single(rem_old, rem_new):
            res = self.__bucket.copy_object(self.__bucket.bucket_name, rem_old, rem_new)
            if res.status >= 400:
                on_error(rem_old, rem_new, res) if on_error else None
            res = self.__bucket.delete_object(rem_old)
            if res.status >= 400:
                on_error("move", rem_old, rem_new, res) if on_error else None
            else:
                # print("object moved | \"" + rem_old + "\" --> \"" + rem_new + "\"")
                on_success("move", rem_old, rem_new, res) if on_success else None
        try:
            remote_old = self.norm_path(remote_old)
            remote_new = self.norm_path(remote_new)
            if self.is_dir(remote_old):
                if not self.is_dir(remote_new):
                    raise YuiMoveException("destination path should also be a directory")
                if remote_new.startswith(remote_old):
                    raise YuiMoveException("destination directory is a sub-directory of the source directory")
                prefix = self.SEP.join(remote_old.strip(self.SEP).split(self.SEP)[:-1]) + self.SEP
                for subdir in self.list_dir(remote_old, True):
                    new_path = remote_new + subdir.key if prefix == self.SEP else subdir.key.replace(prefix, remote_new)
                    move_single(subdir.key, new_path)
            else:
                if self.is_dir(remote_new):
                    prefix = self.SEP.join(remote_old.strip(self.SEP).split(self.SEP)[:-1]) + self.SEP
                    remote_new = remote_old.replace(prefix, remote_new)
                move_single(remote_old, remote_new)

        except Exception as e:
            raise YuiMoveException(e)

    def list_dir(self, root, list_all=False):
        """
        return object iterator with specified prefix and/or delimiter
        :param root:
        :param list_all: if is True, all subdirectories and files will be returned, else only children directories and files
        :return:
        """
        try:
            return oss2.ObjectIterator(self.__bucket, root, '' if list_all else self.SEP)
        except Exception as e:
            raise YuiListDirException(e)

    @staticmethod
    def norm_path(remote_path):
        """
        normalize remote path
        e.g. foo/bar/ --directory
        e.g. foo/bar/foobar.txt --file
        :param remote_path:
        :return:
        """
        remote_path = remote_path.strip()
        isdir = True if remote_path.endswith((os.sep, OssFileManager.SEP)) or remote_path.strip() in ['', '.', '..'] else False
        remote_path = os.path.normpath(remote_path).replace(os.sep, OssFileManager.SEP)
        if remote_path == '' or remote_path == OssFileManager.SEP:
            return OssFileManager.SEP
        if isdir:
            remote_path += OssFileManager.SEP
        return remote_path

    @staticmethod
    def is_dir(remote_path):
        """
        judge if a remote_path is a dir by if it ends with '/'
        :param remote_path:
        :return:
        """
        return True if remote_path.strip() in ['', '.', '..'] \
            else OssFileManager.norm_path(remote_path).endswith(OssFileManager.SEP)

    @staticmethod
    def md5_to_base64(md5_str):
        return base64.b64encode(binascii.a2b_hex(md5_str.encode())).decode()

    @staticmethod
    def base64_to_md5(b64):
        return binascii.b2a_hex(base64.b64decode(b64.encode())).decode()
