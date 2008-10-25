#
# Loxodo -- Password Safe V3 compatible Password Vault
# Copyright (C) 2008 Christoph Sommer <mail@christoph-sommer.de>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#

import sys
import hashlib
import struct
from hmac import HMAC
import random
import os
import tempfile

from twofish.twofish_ecb import TwofishECB
from twofish.twofish_cbc import TwofishCBC

class Vault(object):

    """
    Represents a collection of password Records in PasswordSafe V3 format.

    The on-disk represenation of the Vault is described in the following file:
    http://passwordsafe.svn.sourceforge.net/viewvc/passwordsafe/trunk/pwsafe/pwsafe/docs/formatV3.txt?revision=2139
    """

    def __init__(self):
        self.f_tag = None
        self.f_salt = None
        self.f_iter = None
        self.f_sha_ps = None
        self.f_b1 = None
        self.f_b2 = None
        self.f_b3 = None
        self.f_b4 = None
        self.f_iv = None
        self.f_hmac = None
        self.header = self.Header()
        self.records = []

    class BadPasswordError(RuntimeError):
        pass

    class VaultFormatError(RuntimeError):
        pass

    class VaultVersionError(VaultFormatError):
        pass

    class Field(object):

        """
        Contains the raw, on-disk representation of a record's field.
        """

        def __init__(self, raw_type, raw_len, raw_value):
            self.raw_type = raw_type
            self.raw_len = raw_len
            self.raw_value = raw_value

    class Header(object):

        """
        Contains the fields of a Vault header.
        """

        def __init__(self):
            self.raw_fields = {}

        def add_raw_field(self, raw_field):
            self.raw_fields[raw_field.raw_type] = raw_field

    class Record(object):

        """
        Contains the fields of an individual password record.
        """

        def __init__(self):
            self.raw_fields = {}
            self._group = ""
            self._title = ""
            self._user = ""
            self._notes = ""
            self._passwd = ""

        def add_raw_field(self, raw_field):
            self.raw_fields[raw_field.raw_type] = raw_field
            if (raw_field.raw_type == 0x02):
                self._group = raw_field.raw_value.decode('utf_8', 'replace')
            if (raw_field.raw_type == 0x03):
                self._title = raw_field.raw_value.decode('utf_8', 'replace')
            if (raw_field.raw_type == 0x04):
                self._user = raw_field.raw_value.decode('utf_8', 'replace')
            if (raw_field.raw_type == 0x05):
                self._notes = raw_field.raw_value.decode('utf_8', 'replace')
            if (raw_field.raw_type == 0x06):
                self._passwd = raw_field.raw_value.decode('utf_8', 'replace')

        def _get_group(self):
            return self._group

        # TODO: refactor Record._set_xyz methods to be less repetitive

        def _set_group(self, value):
            self._group = value
            raw_id = 0x02
            if (not self.raw_fields.has_key(raw_id)):
                self.raw_fields[raw_id] = Vault.Field(raw_id, len(value), value)
            self.raw_fields[raw_id].raw_value = value.encode('utf_8', 'replace')
            self.raw_fields[raw_id].raw_len = len(self.raw_fields[raw_id].raw_value)

        def _get_title(self):
            return self._title

        def _set_title(self, value):
            self._title = value
            raw_id = 0x03
            if (not self.raw_fields.has_key(raw_id)):
                self.raw_fields[raw_id] = Vault.Field(raw_id, len(value), value)
            self.raw_fields[raw_id].raw_value = value.encode('utf_8', 'replace')
            self.raw_fields[raw_id].raw_len = len(self.raw_fields[raw_id].raw_value)

        def _get_user(self):
            return self._user

        def _set_user(self, value):
            self._user = value
            raw_id = 0x04
            if (not self.raw_fields.has_key(raw_id)):
                self.raw_fields[raw_id] = Vault.Field(raw_id, len(value), value)
            self.raw_fields[raw_id].raw_value = value.encode('utf_8', 'replace')
            self.raw_fields[raw_id].raw_len = len(self.raw_fields[raw_id].raw_value)

        def _get_notes(self):
            return self._notes

        def _set_notes(self, value):
            self._notes = value
            raw_id = 0x05
            if (not self.raw_fields.has_key(raw_id)):
                self.raw_fields[raw_id] = Vault.Field(raw_id, len(value), value)
            self.raw_fields[raw_id].raw_value = value.encode('utf_8', 'replace')
            self.raw_fields[raw_id].raw_len = len(self.raw_fields[raw_id].raw_value)

        def _get_passwd(self):
            return self._passwd

        def _set_passwd(self, value):
            self._passwd = value
            raw_id = 0x06
            if (not self.raw_fields.has_key(raw_id)):
                self.raw_fields[raw_id] = Vault.Field(raw_id, len(value), value)
            self.raw_fields[raw_id].raw_value = value.encode('utf_8', 'replace')
            self.raw_fields[raw_id].raw_len = len(self.raw_fields[raw_id].raw_value)

        group = property(_get_group, _set_group)
        title = property(_get_title, _set_title)
        user = property(_get_user, _set_user)
        notes = property(_get_notes, _set_notes)
        passwd = property(_get_passwd, _set_passwd)

    def _stretch_password(self, password, salt, iterations):
        """
        Generate the SHA-256 value of a password after several rounds of stretching.

        The algorithm is described in the following paper:
        [KEYSTRETCH Section 4.1] http://www.schneier.com/paper-low-entropy.pdf
        """
        sha = hashlib.sha256()
        sha.update(password)
        sha.update(salt)
        stretched_password = sha.digest()
        for dummy in range(iterations):
            stretched_password = hashlib.sha256(stretched_password).digest()
        return stretched_password

    def _read_field_tlv(self, filehandle, cipher):
        """
        Return one field of a vault record by reading from the given file handle.
        """
        data = filehandle.read(16)
        if not data:
            raise self.VaultFormatError("EOF encountered when parsing record field")
        if data == "PWS3-EOFPWS3-EOF":
            return None
        data = cipher.decrypt(data)
        raw_len = struct.unpack("<L", data[0:4])[0]
        raw_type = struct.unpack("<B", data[4])[0]
        raw_value = data[5:]
        if (raw_len > 11):
            if (raw_len > 1024):
                print "Emergency Exit"
                sys.exit(1)
            for dummy in range((raw_len+4)//16):
                data = filehandle.read(16)
                if not data:
                    return None
                raw_value += cipher.decrypt(data)
        raw_value = raw_value[:raw_len]
        return self.Field(raw_type, raw_len, raw_value)

    def _urandom(self, count):
        try:
            return os.urandom(count)
        except NotImplementedError:
            retval = ""
            for dummy in range(count):
                retval += struct.pack("<B", random.randint(0, 0xFF))
            return retval

    def _write_field_tlv(self, filehandle, cipher, field):
        """
        Write one field of a vault record using the given file handle.
        """
        if (field is None):
            filehandle.write("PWS3-EOFPWS3-EOF")
            return

        assert len(field.raw_value) == field.raw_len

        raw_len = struct.pack("<L", field.raw_len)
        raw_type = struct.pack("<B", field.raw_type)
        raw_value = field.raw_value

        # Assemble TLV block and pad to 16-byte boundary
        data = raw_len + raw_type + raw_value
        if (len(data) % 16 != 0):
            pad_count = 16 - (len(data) % 16)
            data += self._urandom(pad_count)

        data = cipher.encrypt(data)

        filehandle.write(data)

    def read_from_file(self, filename, password):
        """
        Initialize all class members by loading the contents of a Vault stored in the given file.
        """
        filehandle = file(filename, 'rb')

        # read boilerplate

        self.f_tag = filehandle.read(4)  # TAG: magic tag
        if (self.f_tag != 'PWS3'):
            raise self.VaultVersionError("Not a PasswordSafe V3 file")

        self.f_salt = filehandle.read(32)  # SALT: SHA-256 salt
        self.f_iter = struct.unpack("<L", filehandle.read(4))[0]  # ITER: SHA-256 keystretch iterations
        stretched_password = self._stretch_password(password, self.f_salt, self.f_iter)  # P': the stretched key
        my_sha_ps = hashlib.sha256(stretched_password).digest()

        self.f_sha_ps = filehandle.read(32) # H(P'): SHA-256 hash of stretched passphrase
        if (self.f_sha_ps != my_sha_ps):
            raise self.BadPasswordError("Wrong password")

        self.f_b1 = filehandle.read(16)  # B1
        self.f_b2 = filehandle.read(16)  # B2
        self.f_b3 = filehandle.read(16)  # B3
        self.f_b4 = filehandle.read(16)  # B4

        cipher = TwofishECB(stretched_password)
        key_k = cipher.decrypt(self.f_b1) + cipher.decrypt(self.f_b2)
        key_l = cipher.decrypt(self.f_b3) + cipher.decrypt(self.f_b4)

        self.f_iv = filehandle.read(16)  # IV: initialization vector of Twofish CBC

        hmac_checker = HMAC(key_l, "", hashlib.sha256)
        cipher = TwofishCBC(key_k, self.f_iv)

        # read header

        while (True):
            field = self._read_field_tlv(filehandle, cipher)
            if not field:
                break
            if field.raw_type == 0xff:
                break
            self.header.add_raw_field(field)
            hmac_checker.update(field.raw_value)

        # read fields

        current_record = self.Record()
        while (True):
            field = self._read_field_tlv(filehandle, cipher)
            if not field:
                break
            if field.raw_type == 0xff:
                self.records.append(current_record)
                current_record = self.Record()
            else:
                hmac_checker.update(field.raw_value)
                current_record.add_raw_field(field)


        # read HMAC

        self.f_hmac = filehandle.read(32)  # HMAC: used to verify Vault's integrity

        my_hmac = hmac_checker.digest()
        if (self.f_hmac != my_hmac):
            raise self.VaultFormatError("File integrity check failed")

        filehandle.close()

    def write_to_file(self, filename, password):
        """
        Store contents of this Vault into a file.
        """
        
        # write to temporary file first
        (osfilehandle, tmpfilename) = tempfile.mkstemp('.part', os.path.basename(filename) + ".", os.path.dirname(filename), text=False)
        filehandle = os.fdopen(osfilehandle, "wb")

        # FIXME: choose new SALT, B1-B4, IV values on each file write? Conflicting Specs!

        # write boilerplate

        filehandle.write(self.f_tag)
        filehandle.write(self.f_salt)
        filehandle.write(struct.pack("<L", self.f_iter))

        stretched_password = self._stretch_password(password, self.f_salt, self.f_iter)
        self.f_sha_ps = hashlib.sha256(stretched_password).digest()
        filehandle.write(self.f_sha_ps)

        filehandle.write(self.f_b1)
        filehandle.write(self.f_b2)
        filehandle.write(self.f_b3)
        filehandle.write(self.f_b4)

        cipher = TwofishECB(stretched_password)
        key_k = cipher.decrypt(self.f_b1) + cipher.decrypt(self.f_b2)
        key_l = cipher.decrypt(self.f_b3) + cipher.decrypt(self.f_b4)

        filehandle.write(self.f_iv)

        hmac_checker = HMAC(key_l, "", hashlib.sha256)
        cipher = TwofishCBC(key_k, self.f_iv)

        end_of_record = self.Field(0xff, 0, "")

        for field in self.header.raw_fields.values():
            self._write_field_tlv(filehandle, cipher, field)
            hmac_checker.update(field.raw_value)
        self._write_field_tlv(filehandle, cipher, end_of_record)
        hmac_checker.update(end_of_record.raw_value)

        for record in self.records:
            for field in record.raw_fields.values():
                self._write_field_tlv(filehandle, cipher, field)
                hmac_checker.update(field.raw_value)
            self._write_field_tlv(filehandle, cipher, end_of_record)
            hmac_checker.update(end_of_record.raw_value)

        self._write_field_tlv(filehandle, cipher, None)

        self.f_hmac = hmac_checker.digest()
        filehandle.write(self.f_hmac)
        filehandle.close()

        try:
            tmpvault = Vault()
            tmpvault.read_from_file(tmpfilename, password)
        except RuntimeError:
            os.remove(tmpfilename)
            raise self.VaultFormatError("File integrity check failed")

        # after writing the temporary file, replace the original file with it
        os.remove(filename)
        os.rename(tmpfilename, filename)