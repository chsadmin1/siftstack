# Endpoint Index - DataSift API Core

Every operation in the DataSift API Core reference (developers.datasift.ai/datasift/), grouped by resource. Base URL: `https://apiv2.reisift.io`. All operations authenticate with `Authorization: Api-Key YOUR_OPEN_API_KEY`.

Total operations: 572 (app-internal auth endpoints omitted). Generated from the published reference on 2026-07-20.

## User (1)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/user/` | retrieveUser |

## Account (2)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/account/{uuid}/` | retrieveAccountUpdate |
| POST | `/api/marketing/trial-signup/` | createMarketingSignup - Public endpoint that creates a trial Account from the marketing site. |

## Addons (6)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/addon/` | listAddons |
| GET | `/api/internal/addon/{type}/` | retrieveAddon |
| PUT | `/api/internal/addon/{type}/` | updateAddon |
| PATCH | `/api/internal/addon/{type}/` | partialUpdateAddon |
| GET | `/api/internal/addon/{type}/preview-invoice/` | previewInvoiceAddon |
| POST | `/api/internal/addon/{type}/subscribe/` | subscribeAddon |

## Property (50)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/property/` | listProperties |
| POST | `/api/internal/property/` | createProperty |
| GET | `/api/internal/property/logs/` | None |
| GET | `/api/internal/property/autocomplete/` | autocompleteProperty |
| GET | `/api/internal/property/neighborhoods/` | neighborhoodsProperty - Distinct neighborhoods present in the account's property records. |
| GET | `/api/internal/property/{uuid}/` | retrieveProperty |
| PATCH | `/api/internal/property/{uuid}/` | partialUpdateProperty |
| DELETE | `/api/internal/property/{uuid}/` | destroyProperty |
| GET | `/api/internal/property/{uuid}/deal/` | dealProperty |
| GET | `/api/internal/property/{uuid}/logs/` | logsProperty |
| GET | `/api/internal/property/{uuid}/next/` | nextProperty |
| POST | `/api/internal/property/{uuid}/next/` | nextProperty |
| GET | `/api/internal/property/{uuid}/prev/` | prevProperty |
| POST | `/api/internal/property/{uuid}/prev/` | prevProperty |
| POST | `/api/internal/property/bulk-create/` | bulkCreateProperty |
| POST | `/api/internal/property/calltools/` | calltoolsProperty |
| POST | `/api/internal/property/exists/` | existsProperty - Check if a property exists in the account by its reapi_id or sift_id |
| POST | `/api/internal/property/address-info/` | getAddressInfoProperty |
| POST | `/api/internal/property/address-info-from-map-id/` | getAddressInfoFromMapIdProperty |
| POST | `/api/internal/property/reirail/` | reirailProperty |
| POST | `/api/internal/property/skip-trace/` | skipTraceProperty |
| POST | `/api/internal/property/smarter_contact/` | smarterContactProperty |
| POST | `/api/internal/property/smrtdialer/` | smrtdialerProperty |
| POST | `/api/internal/property/xencall/` | xencallProperty |
| POST | `/api/internal/property/{uuid}/add-lists/` | addListsDetailProperty |
| POST | `/api/internal/property/{uuid}/add-notes/` | addNotesDetailProperty |
| POST | `/api/internal/property/{uuid}/add-phone-tag/` | addPhoneTagProperty |
| POST | `/api/internal/property/{uuid}/add-tags/` | addTagsDetailProperty |
| POST | `/api/internal/property/{uuid}/assign/` | assignProperty |
| POST | `/api/internal/property/{uuid}/do-not-mail-ever/` | doNotMailEverProperty |
| POST | `/api/internal/property/{uuid}/hotness/` | hotnessDetailProperty |
| POST | `/api/internal/property/{uuid}/predictivecall-attempts/` | predictivecallAttemptsProperty |
| POST | `/api/internal/property/{uuid}/remove-lists/` | removeListsDetailProperty |
| POST | `/api/internal/property/{uuid}/remove-tags/` | removeTagsDetailProperty |
| POST | `/api/internal/property/{uuid}/rvm-attempts/` | rvmAttemptsProperty |
| POST | `/api/internal/property/{uuid}/sms-attempts/` | smsAttemptsProperty |
| POST | `/api/internal/property/{uuid}/status/` | statusDetailProperty |
| GET | `/api/internal/property/{property_uuid}/custom-field/` | listCustomFieldValues |
| PATCH | `/api/internal/property/{property_uuid}/custom-field/update-values/` | updateValuesCustomFieldValue - PATCH /property/{property_uuid}/custom-field-value/update-values/ |
| GET | `/api/internal/property/{property_uuid}/document/` | listDocuments |
| POST | `/api/internal/property/{property_uuid}/document/` | createDocument |
| GET | `/api/internal/property/{property_uuid}/document/presigned-url/` | presignedUrlDocument |
| GET | `/api/internal/property/{property_uuid}/document/{uuid}/` | retrieveDocument |
| DELETE | `/api/internal/property/{property_uuid}/document/{uuid}/` | destroyDocument |
| GET | `/api/internal/property/{property_uuid}/image/` | listImages |
| POST | `/api/internal/property/{property_uuid}/image/` | createImage |
| GET | `/api/internal/property/{property_uuid}/image/presigned-url/` | presignedUrlImage |
| GET | `/api/internal/property/{property_uuid}/image/{uuid}/` | retrieveImage |
| DELETE | `/api/internal/property/{property_uuid}/image/{uuid}/` | destroyImage |
| POST | `/api/internal/property/{property_uuid}/image/{uuid}/set-cover/` | setCoverImage |

## Properties Workspace (records, presets, lists, tags) (109)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/properties/filter-preset/{filter_preset_uuid}/scheduled-export/` | retrieveFilterPresetScheduledExport |
| POST | `/api/internal/properties/filter-preset/{filter_preset_uuid}/scheduled-export/` | createFilterPresetScheduledExport |
| PATCH | `/api/internal/properties/filter-preset/{filter_preset_uuid}/scheduled-export/` | partialUpdateFilterPresetScheduledExport |
| DELETE | `/api/internal/properties/filter-preset/{filter_preset_uuid}/scheduled-export/` | destroyFilterPresetScheduledExport |
| GET | `/api/internal/properties/filter-preset/` | listFilterPresets |
| POST | `/api/internal/properties/filter-preset/` | createFilterPreset |
| GET | `/api/internal/properties/filter-preset/{uuid}/` | retrieveFilterPreset |
| PUT | `/api/internal/properties/filter-preset/{uuid}/` | updateFilterPreset |
| PATCH | `/api/internal/properties/filter-preset/{uuid}/` | partialUpdateFilterPreset |
| DELETE | `/api/internal/properties/filter-preset/{uuid}/` | destroyFilterPreset |
| GET | `/api/internal/properties/filter-preset-folder/` | listFilterPresetFolders |
| POST | `/api/internal/properties/filter-preset-folder/` | createFilterPresetFolder |
| GET | `/api/internal/properties/filter-preset-folder/{uuid}/` | retrieveFilterPresetFolder |
| PUT | `/api/internal/properties/filter-preset-folder/{uuid}/` | updateFilterPresetFolder |
| PATCH | `/api/internal/properties/filter-preset-folder/{uuid}/` | partialUpdateFilterPresetFolder |
| DELETE | `/api/internal/properties/filter-preset-folder/{uuid}/` | destroyFilterPresetFolder |
| GET | `/api/internal/properties/filter-preset-folder/{folder_uuid}/filter-preset/` | listFilterPresets |
| POST | `/api/internal/properties/filter-preset-folder/{folder_uuid}/filter-preset/` | createFilterPreset |
| GET | `/api/internal/properties/filter-preset-folder/{folder_uuid}/filter-preset/{uuid}/` | retrieveFilterPreset |
| PUT | `/api/internal/properties/filter-preset-folder/{folder_uuid}/filter-preset/{uuid}/` | updateFilterPreset |
| PATCH | `/api/internal/properties/filter-preset-folder/{folder_uuid}/filter-preset/{uuid}/` | partialUpdateFilterPreset |
| DELETE | `/api/internal/properties/filter-preset-folder/{folder_uuid}/filter-preset/{uuid}/` | destroyFilterPreset |
| POST | `/api/internal/properties/filter-preset/compile/` | createFilterPresetCompile |
| GET | `/api/internal/properties/list/` | listLists |
| POST | `/api/internal/properties/list/` | createList |
| GET | `/api/internal/properties/list/{uuid}/` | retrieveList |
| PATCH | `/api/internal/properties/list/{uuid}/` | partialUpdateList |
| DELETE | `/api/internal/properties/list/{uuid}/` | destroyList |
| GET | `/api/internal/properties/list/{uuid}/properties-count/` | propertiesCountList |
| GET | `/api/internal/properties/list-folder/` | listListFolders |
| POST | `/api/internal/properties/list-folder/` | createListFolder |
| GET | `/api/internal/properties/list-folder/{uuid}/` | retrieveListFolder |
| PUT | `/api/internal/properties/list-folder/{uuid}/` | updateListFolder |
| PATCH | `/api/internal/properties/list-folder/{uuid}/` | partialUpdateListFolder |
| DELETE | `/api/internal/properties/list-folder/{uuid}/` | destroyListFolder |
| GET | `/api/internal/properties/list-folder/{folder_uuid}/list/` | listLists |
| POST | `/api/internal/properties/list-folder/{folder_uuid}/list/` | createList |
| GET | `/api/internal/properties/list-folder/{folder_uuid}/list/{uuid}/` | retrieveList |
| PATCH | `/api/internal/properties/list-folder/{folder_uuid}/list/{uuid}/` | partialUpdateList |
| DELETE | `/api/internal/properties/list-folder/{folder_uuid}/list/{uuid}/` | destroyList |
| GET | `/api/internal/properties/list-folder/{folder_uuid}/list/{uuid}/properties-count/` | propertiesCountList |
| GET | `/api/internal/properties/tag/` | listTags |
| POST | `/api/internal/properties/tag/` | createTag |
| GET | `/api/internal/properties/tag/{uuid}/` | retrieveTag |
| PATCH | `/api/internal/properties/tag/{uuid}/` | partialUpdateTag |
| DELETE | `/api/internal/properties/tag/{uuid}/` | destroyTag |
| GET | `/api/internal/properties/tag/{uuid}/properties-count/` | propertiesCountTag |
| GET | `/api/internal/properties/tag-folder/` | listTagFolders |
| POST | `/api/internal/properties/tag-folder/` | createTagFolder |
| GET | `/api/internal/properties/tag-folder/{uuid}/` | retrieveTagFolder |
| PUT | `/api/internal/properties/tag-folder/{uuid}/` | updateTagFolder |
| PATCH | `/api/internal/properties/tag-folder/{uuid}/` | partialUpdateTagFolder |
| DELETE | `/api/internal/properties/tag-folder/{uuid}/` | destroyTagFolder |
| GET | `/api/internal/properties/tag-folder/{folder_uuid}/tag/` | listTags |
| POST | `/api/internal/properties/tag-folder/{folder_uuid}/tag/` | createTag |
| GET | `/api/internal/properties/tag-folder/{folder_uuid}/tag/{uuid}/` | retrieveTag |
| PATCH | `/api/internal/properties/tag-folder/{folder_uuid}/tag/{uuid}/` | partialUpdateTag |
| DELETE | `/api/internal/properties/tag-folder/{folder_uuid}/tag/{uuid}/` | destroyTag |
| GET | `/api/internal/properties/tag-folder/{folder_uuid}/tag/{uuid}/properties-count/` | propertiesCountTag |
| GET | `/api/internal/properties/property/` | listProperties |
| POST | `/api/internal/properties/property/` | createProperty |
| GET | `/api/internal/properties/property/logs/` | None |
| GET | `/api/internal/properties/property/autocomplete/` | autocompleteProperty |
| GET | `/api/internal/properties/property/neighborhoods/` | neighborhoodsProperty - Distinct neighborhoods present in the account's property records. |
| GET | `/api/internal/properties/property/{uuid}/` | retrieveProperty |
| PATCH | `/api/internal/properties/property/{uuid}/` | partialUpdateProperty |
| DELETE | `/api/internal/properties/property/{uuid}/` | destroyProperty |
| GET | `/api/internal/properties/property/{uuid}/deal/` | dealProperty |
| GET | `/api/internal/properties/property/{uuid}/logs/` | logsProperty |
| GET | `/api/internal/properties/property/{uuid}/next/` | nextProperty |
| POST | `/api/internal/properties/property/{uuid}/next/` | nextProperty |
| GET | `/api/internal/properties/property/{uuid}/prev/` | prevProperty |
| POST | `/api/internal/properties/property/{uuid}/prev/` | prevProperty |
| POST | `/api/internal/properties/property/bulk-create/` | bulkCreateProperty |
| POST | `/api/internal/properties/property/calltools/` | calltoolsProperty |
| POST | `/api/internal/properties/property/exists/` | existsProperty - Check if a property exists in the account by its reapi_id or sift_id |
| POST | `/api/internal/properties/property/address-info/` | getAddressInfoProperty |
| POST | `/api/internal/properties/property/address-info-from-map-id/` | getAddressInfoFromMapIdProperty |
| POST | `/api/internal/properties/property/reirail/` | reirailProperty |
| POST | `/api/internal/properties/property/skip-trace/` | skipTraceProperty |
| POST | `/api/internal/properties/property/smarter_contact/` | smarterContactProperty |
| POST | `/api/internal/properties/property/smrtdialer/` | smrtdialerProperty |
| POST | `/api/internal/properties/property/xencall/` | xencallProperty |
| POST | `/api/internal/properties/property/{uuid}/add-lists/` | addListsDetailProperty |
| POST | `/api/internal/properties/property/{uuid}/add-notes/` | addNotesDetailProperty |
| POST | `/api/internal/properties/property/{uuid}/add-phone-tag/` | addPhoneTagProperty |
| POST | `/api/internal/properties/property/{uuid}/add-tags/` | addTagsDetailProperty |
| POST | `/api/internal/properties/property/{uuid}/assign/` | assignProperty |
| POST | `/api/internal/properties/property/{uuid}/do-not-mail-ever/` | doNotMailEverProperty |
| POST | `/api/internal/properties/property/{uuid}/hotness/` | hotnessDetailProperty |
| POST | `/api/internal/properties/property/{uuid}/predictivecall-attempts/` | predictivecallAttemptsProperty |
| POST | `/api/internal/properties/property/{uuid}/remove-lists/` | removeListsDetailProperty |
| POST | `/api/internal/properties/property/{uuid}/remove-tags/` | removeTagsDetailProperty |
| POST | `/api/internal/properties/property/{uuid}/rvm-attempts/` | rvmAttemptsProperty |
| POST | `/api/internal/properties/property/{uuid}/sms-attempts/` | smsAttemptsProperty |
| POST | `/api/internal/properties/property/{uuid}/status/` | statusDetailProperty |
| GET | `/api/internal/properties/property/{property_uuid}/custom-field/` | listCustomFieldValues |
| PATCH | `/api/internal/properties/property/{property_uuid}/custom-field/update-values/` | updateValuesCustomFieldValue - PATCH /property/{property_uuid}/custom-field-value/update-values/ |
| GET | `/api/internal/properties/property/{property_uuid}/document/` | listDocuments |
| POST | `/api/internal/properties/property/{property_uuid}/document/` | createDocument |
| GET | `/api/internal/properties/property/{property_uuid}/document/presigned-url/` | presignedUrlDocument |
| GET | `/api/internal/properties/property/{property_uuid}/document/{uuid}/` | retrieveDocument |
| DELETE | `/api/internal/properties/property/{property_uuid}/document/{uuid}/` | destroyDocument |
| GET | `/api/internal/properties/property/{property_uuid}/image/` | listImages |
| POST | `/api/internal/properties/property/{property_uuid}/image/` | createImage |
| GET | `/api/internal/properties/property/{property_uuid}/image/presigned-url/` | presignedUrlImage |
| GET | `/api/internal/properties/property/{property_uuid}/image/{uuid}/` | retrieveImage |
| DELETE | `/api/internal/properties/property/{property_uuid}/image/{uuid}/` | destroyImage |
| POST | `/api/internal/properties/property/{property_uuid}/image/{uuid}/set-cover/` | setCoverImage |

## Filter Presets (23)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/filter-preset/{filter_preset_uuid}/scheduled-export/` | retrieveFilterPresetScheduledExport |
| POST | `/api/internal/filter-preset/{filter_preset_uuid}/scheduled-export/` | createFilterPresetScheduledExport |
| PATCH | `/api/internal/filter-preset/{filter_preset_uuid}/scheduled-export/` | partialUpdateFilterPresetScheduledExport |
| DELETE | `/api/internal/filter-preset/{filter_preset_uuid}/scheduled-export/` | destroyFilterPresetScheduledExport |
| GET | `/api/internal/filter-preset/` | listFilterPresets |
| POST | `/api/internal/filter-preset/` | createFilterPreset |
| GET | `/api/internal/filter-preset/{uuid}/` | retrieveFilterPreset |
| PUT | `/api/internal/filter-preset/{uuid}/` | updateFilterPreset |
| PATCH | `/api/internal/filter-preset/{uuid}/` | partialUpdateFilterPreset |
| DELETE | `/api/internal/filter-preset/{uuid}/` | destroyFilterPreset |
| GET | `/api/internal/filter-preset-folder/` | listFilterPresetFolders |
| POST | `/api/internal/filter-preset-folder/` | createFilterPresetFolder |
| GET | `/api/internal/filter-preset-folder/{uuid}/` | retrieveFilterPresetFolder |
| PUT | `/api/internal/filter-preset-folder/{uuid}/` | updateFilterPresetFolder |
| PATCH | `/api/internal/filter-preset-folder/{uuid}/` | partialUpdateFilterPresetFolder |
| DELETE | `/api/internal/filter-preset-folder/{uuid}/` | destroyFilterPresetFolder |
| GET | `/api/internal/filter-preset-folder/{folder_uuid}/filter-preset/` | listFilterPresets |
| POST | `/api/internal/filter-preset-folder/{folder_uuid}/filter-preset/` | createFilterPreset |
| GET | `/api/internal/filter-preset-folder/{folder_uuid}/filter-preset/{uuid}/` | retrieveFilterPreset |
| PUT | `/api/internal/filter-preset-folder/{folder_uuid}/filter-preset/{uuid}/` | updateFilterPreset |
| PATCH | `/api/internal/filter-preset-folder/{folder_uuid}/filter-preset/{uuid}/` | partialUpdateFilterPreset |
| DELETE | `/api/internal/filter-preset-folder/{folder_uuid}/filter-preset/{uuid}/` | destroyFilterPreset |
| POST | `/api/internal/filter-preset/compile/` | createFilterPresetCompile |

## Lists (18)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/list/` | listLists |
| POST | `/api/internal/list/` | createList |
| GET | `/api/internal/list/{uuid}/` | retrieveList |
| PATCH | `/api/internal/list/{uuid}/` | partialUpdateList |
| DELETE | `/api/internal/list/{uuid}/` | destroyList |
| GET | `/api/internal/list/{uuid}/properties-count/` | propertiesCountList |
| GET | `/api/internal/list-folder/` | listListFolders |
| POST | `/api/internal/list-folder/` | createListFolder |
| GET | `/api/internal/list-folder/{uuid}/` | retrieveListFolder |
| PUT | `/api/internal/list-folder/{uuid}/` | updateListFolder |
| PATCH | `/api/internal/list-folder/{uuid}/` | partialUpdateListFolder |
| DELETE | `/api/internal/list-folder/{uuid}/` | destroyListFolder |
| GET | `/api/internal/list-folder/{folder_uuid}/list/` | listLists |
| POST | `/api/internal/list-folder/{folder_uuid}/list/` | createList |
| GET | `/api/internal/list-folder/{folder_uuid}/list/{uuid}/` | retrieveList |
| PATCH | `/api/internal/list-folder/{folder_uuid}/list/{uuid}/` | partialUpdateList |
| DELETE | `/api/internal/list-folder/{folder_uuid}/list/{uuid}/` | destroyList |
| GET | `/api/internal/list-folder/{folder_uuid}/list/{uuid}/properties-count/` | propertiesCountList |

## Tags (18)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/tag/` | listTags |
| POST | `/api/internal/tag/` | createTag |
| GET | `/api/internal/tag/{uuid}/` | retrieveTag |
| PATCH | `/api/internal/tag/{uuid}/` | partialUpdateTag |
| DELETE | `/api/internal/tag/{uuid}/` | destroyTag |
| GET | `/api/internal/tag/{uuid}/properties-count/` | propertiesCountTag |
| GET | `/api/internal/tag-folder/` | listTagFolders |
| POST | `/api/internal/tag-folder/` | createTagFolder |
| GET | `/api/internal/tag-folder/{uuid}/` | retrieveTagFolder |
| PUT | `/api/internal/tag-folder/{uuid}/` | updateTagFolder |
| PATCH | `/api/internal/tag-folder/{uuid}/` | partialUpdateTagFolder |
| DELETE | `/api/internal/tag-folder/{uuid}/` | destroyTagFolder |
| GET | `/api/internal/tag-folder/{folder_uuid}/tag/` | listTags |
| POST | `/api/internal/tag-folder/{folder_uuid}/tag/` | createTag |
| GET | `/api/internal/tag-folder/{folder_uuid}/tag/{uuid}/` | retrieveTag |
| PATCH | `/api/internal/tag-folder/{folder_uuid}/tag/{uuid}/` | partialUpdateTag |
| DELETE | `/api/internal/tag-folder/{folder_uuid}/tag/{uuid}/` | destroyTag |
| GET | `/api/internal/tag-folder/{folder_uuid}/tag/{uuid}/properties-count/` | propertiesCountTag |

## Global Statuses (8)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/global-status/` | listGlobalStatuses |
| GET | `/api/internal/global-status/{uuid}/` | retrieveGlobalStatus |
| PUT | `/api/internal/global-status/{uuid}/` | updateGlobalStatus |
| PATCH | `/api/internal/global-status/{uuid}/` | partialUpdateGlobalStatus |
| GET | `/api/internal/properties/global-status/` | listGlobalStatuses |
| GET | `/api/internal/properties/global-status/{uuid}/` | retrieveGlobalStatus |
| PUT | `/api/internal/properties/global-status/{uuid}/` | updateGlobalStatus |
| PATCH | `/api/internal/properties/global-status/{uuid}/` | partialUpdateGlobalStatus |

## Custom Fields (15)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/custom-fields/group/` | listCustomFieldGroups |
| POST | `/api/internal/custom-fields/group/` | createCustomFieldGroup |
| GET | `/api/internal/custom-fields/group/{id}/` | retrieveCustomFieldGroup |
| PATCH | `/api/internal/custom-fields/group/{id}/` | partialUpdateCustomFieldGroup |
| DELETE | `/api/internal/custom-fields/group/{id}/` | destroyCustomFieldGroup |
| GET | `/api/internal/custom-fields/` | listCustomFields |
| POST | `/api/internal/custom-fields/` | createCustomField |
| GET | `/api/internal/custom-fields/{id}/` | retrieveCustomField |
| PATCH | `/api/internal/custom-fields/{id}/` | partialUpdateCustomField |
| DELETE | `/api/internal/custom-fields/{id}/` | destroyCustomField |
| GET | `/api/internal/custom-fields/{field_id}/option/` | listCustomFieldOptions |
| POST | `/api/internal/custom-fields/{field_id}/option/` | createCustomFieldOption |
| GET | `/api/internal/custom-fields/{field_id}/option/{id}/` | retrieveCustomFieldOption |
| PATCH | `/api/internal/custom-fields/{field_id}/option/{id}/` | partialUpdateCustomFieldOption |
| DELETE | `/api/internal/custom-fields/{field_id}/option/{id}/` | destroyCustomFieldOption |

## Owners (35)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/owner/{owner_uuid}/sms/` | listSMs |
| POST | `/api/internal/owner/{owner_uuid}/sms/` | createSMS |
| POST | `/api/internal/owner/{owner_uuid}/sms/{uuid}/cancel/` | cancelSMS |
| POST | `/api/internal/owner/{owner_uuid}/sms/{uuid}/resend/` | resendSMS |
| GET | `/api/internal/owner/` | listOwners |
| POST | `/api/internal/owner/` | createOwner |
| GET | `/api/internal/owner/{uuid}/` | retrieveOwner |
| PATCH | `/api/internal/owner/{uuid}/` | partialUpdateOwner |
| DELETE | `/api/internal/owner/{uuid}/` | destroyOwner |
| GET | `/api/internal/owner/{uuid}/logs/` | logsOwner |
| GET | `/api/internal/owner/{uuid}/next/` | nextOwner |
| GET | `/api/internal/owner/{uuid}/prev/` | prevOwner |
| POST | `/api/internal/owner/delete/` | createOwner |
| POST | `/api/internal/owner/{uuid}/upsert-emails/` | addEmailsOwner |
| POST | `/api/internal/owner/{uuid}/add-phone-tag/` | addPhoneTagOwner |
| POST | `/api/internal/owner/{uuid}/add-property/` | addPropertyOwner |
| POST | `/api/internal/owner/{uuid}/contact/` | contactOwner |
| POST | `/api/internal/owner/{uuid}/do-not-mail-ever/` | doNotMailEverOwner |
| POST | `/api/internal/owner/{uuid}/predictivecall-attempts/` | predictivecallAttemptsOwner |
| POST | `/api/internal/owner/{uuid}/remove-emails/` | removeEmailsOwner |
| POST | `/api/internal/owner/{uuid}/remove-phones/` | removePhonesOwner |
| POST | `/api/internal/owner/{uuid}/rvm-attempts/` | rvmAttemptsOwner |
| POST | `/api/internal/owner/{uuid}/skiptrace-attempts/` | skiptraceAttemptsOwner |
| POST | `/api/internal/owner/{uuid}/sms-attempts/` | smsAttemptsOwner |
| POST | `/api/internal/owner/{uuid}/upsert-phones/` | upsertPhonesOwner |
| POST | `/api/internal/owner/{uuid}/verify-email/{email}/` | verifyEmailOwner |
| GET | `/api/internal/owner/{owner_uuid}/message/` | listMessages |
| POST | `/api/internal/owner/{owner_uuid}/message/` | createMessage |
| GET | `/api/internal/owner/{owner_uuid}/message/{uuid}/` | retrieveMessage |
| PATCH | `/api/internal/owner/{owner_uuid}/message/{uuid}/` | partialUpdateMessage |
| DELETE | `/api/internal/owner/{owner_uuid}/message/{uuid}/` | destroyMessage |
| POST | `/api/internal/owner/{owner_uuid}/message/{uuid}/pin/` | pinMessage |
| POST | `/api/internal/owner/{owner_uuid}/message/{uuid}/unpin/` | unpinMessage |
| GET | `/api/internal/owner/{owner_uuid}/offer/` | listOffers |
| GET | `/api/internal/owner/{owner_uuid}/offer/{uuid}/` | retrieveOffer |

## Phone Tags and Types (8)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/phone/type/` | listPhoneTypeViewSets |
| POST | `/api/internal/phone/add-phone-tag/` | addPhoneTagPhone |
| GET | `/api/internal/phone/tag/` | listPhoneTags |
| POST | `/api/internal/phone/tag/` | createPhoneTag |
| GET | `/api/internal/phone/tag/{uuid}/` | retrievePhoneTag |
| PATCH | `/api/internal/phone/tag/{uuid}/` | partialUpdatePhoneTag |
| DELETE | `/api/internal/phone/tag/{uuid}/` | destroyPhoneTag |
| GET | `/api/internal/phone/tag/{uuid}/properties-count/` | propertiesCountPhoneTag |

## Contacts (27)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/contacts/contact/` | listContacts |
| POST | `/api/internal/contacts/contact/` | createContact |
| GET | `/api/internal/contacts/contact/{uuid}/` | retrieveContact |
| DELETE | `/api/internal/contacts/contact/{uuid}/` | destroyContact |
| GET | `/api/internal/contacts/contact/{uuid}/next/` | nextContact |
| GET | `/api/internal/contacts/contact/{uuid}/prev/` | prevContact |
| GET | `/api/internal/contacts/tag/` | listTags |
| POST | `/api/internal/contacts/tag/` | createTag |
| GET | `/api/internal/contacts/tag/{uuid}/` | retrieveTag |
| PATCH | `/api/internal/contacts/tag/{uuid}/` | partialUpdateTag |
| DELETE | `/api/internal/contacts/tag/{uuid}/` | destroyTag |
| GET | `/api/internal/contacts/tag/{uuid}/contacts-count/` | contactsCountTag |
| GET | `/api/internal/contacts/tag-folder/` | listTagFolders |
| POST | `/api/internal/contacts/tag-folder/` | createTagFolder |
| GET | `/api/internal/contacts/tag-folder/{uuid}/` | retrieveTagFolder |
| PUT | `/api/internal/contacts/tag-folder/{uuid}/` | updateTagFolder |
| PATCH | `/api/internal/contacts/tag-folder/{uuid}/` | partialUpdateTagFolder |
| DELETE | `/api/internal/contacts/tag-folder/{uuid}/` | destroyTagFolder |
| GET | `/api/internal/contacts/tag-folder/{folder_uuid}/tag/` | listTags |
| POST | `/api/internal/contacts/tag-folder/{folder_uuid}/tag/` | createTag |
| GET | `/api/internal/contacts/tag-folder/{folder_uuid}/tag/{uuid}/` | retrieveTag |
| PATCH | `/api/internal/contacts/tag-folder/{folder_uuid}/tag/{uuid}/` | partialUpdateTag |
| DELETE | `/api/internal/contacts/tag-folder/{folder_uuid}/tag/{uuid}/` | destroyTag |
| GET | `/api/internal/contacts/tag-folder/{folder_uuid}/tag/{uuid}/contacts-count/` | contactsCountTag |
| POST | `/api/internal/contacts/contact/{uuid}/add-tags/` | addTagsDetailContact |
| POST | `/api/internal/contacts/contact/{uuid}/remove-tags/` | removeTagsDetailContact |
| POST | `/api/internal/contacts/contact/{uuid}/status/` | statusDetailContact |

## Tasks (15)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/task/` | listTasks |
| POST | `/api/internal/task/` | createTask |
| GET | `/api/internal/task/{uuid}/` | retrieveTask |
| PUT | `/api/internal/task/{uuid}/` | updateTask |
| PATCH | `/api/internal/task/{uuid}/` | partialUpdateTask |
| DELETE | `/api/internal/task/{uuid}/` | destroyTask |
| GET | `/api/internal/task/{uuid}/property/` | propertyTask |
| POST | `/api/internal/task/bulk-complete/` | bulkCompleteTask |
| POST | `/api/internal/task/create-by-group/` | createByGroupTask |
| POST | `/api/internal/task/create-by-preset/` | createByPresetTask |
| POST | `/api/internal/task/{uuid}/complete/` | completeTask |
| POST | `/api/internal/task/{uuid}/disable-recurrence/` | disableRecurrenceTask |
| POST | `/api/internal/task/{uuid}/enable-recurrence/` | enableRecurrenceTask |
| POST | `/api/internal/task/{uuid}/set-outcome/` | setOutcomeTask - Update appointment outcome (status, result, notes) |
| POST | `/api/internal/task/{uuid}/uncomplete/` | uncompleteTask |

## Task Groups (12)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/task-group/` | listTaskGroups |
| POST | `/api/internal/task-group/` | createTaskGroup |
| GET | `/api/internal/task-group/{uuid}/` | retrieveTaskGroup |
| PUT | `/api/internal/task-group/{uuid}/` | updateTaskGroup |
| PATCH | `/api/internal/task-group/{uuid}/` | partialUpdateTaskGroup |
| DELETE | `/api/internal/task-group/{uuid}/` | destroyTaskGroup |
| GET | `/api/internal/task-group/{task_group_uuid}/task-preset/` | listTaskPresets |
| POST | `/api/internal/task-group/{task_group_uuid}/task-preset/` | createTaskPreset |
| GET | `/api/internal/task-group/{task_group_uuid}/task-preset/{uuid}/` | retrieveTaskPreset |
| PUT | `/api/internal/task-group/{task_group_uuid}/task-preset/{uuid}/` | updateTaskPreset |
| PATCH | `/api/internal/task-group/{task_group_uuid}/task-preset/{uuid}/` | partialUpdateTaskPreset |
| DELETE | `/api/internal/task-group/{task_group_uuid}/task-preset/{uuid}/` | destroyTaskPreset |

## Task Presets (6)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/task-preset/` | listTaskPresets |
| POST | `/api/internal/task-preset/` | createTaskPreset |
| GET | `/api/internal/task-preset/{uuid}/` | retrieveTaskPreset |
| PUT | `/api/internal/task-preset/{uuid}/` | updateTaskPreset |
| PATCH | `/api/internal/task-preset/{uuid}/` | partialUpdateTaskPreset |
| DELETE | `/api/internal/task-preset/{uuid}/` | destroyTaskPreset |

## Task Recurrence (3)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/task-recurrence/` | listRecurrences |
| POST | `/api/internal/task-recurrence/{uuid}/disable/` | disableRecurrence |
| POST | `/api/internal/task-recurrence/{uuid}/enable/` | enableRecurrence |

## SiftLine (50)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/siftline/board/` | listBoards |
| POST | `/api/internal/siftline/board/` | createBoard |
| GET | `/api/internal/siftline/board/{uuid}/` | retrieveBoard |
| PUT | `/api/internal/siftline/board/{uuid}/` | updateBoard |
| PATCH | `/api/internal/siftline/board/{uuid}/` | partialUpdateBoard |
| DELETE | `/api/internal/siftline/board/{uuid}/` | destroyBoard |
| POST | `/api/internal/siftline/board/column/card-bulk/` | createList |
| PUT | `/api/internal/siftline/board/column/card-bulk/{uuid}/` | updateList |
| GET | `/api/internal/siftline/task/` | listTasks |
| POST | `/api/internal/siftline/task/` | createTask |
| GET | `/api/internal/siftline/task/{uuid}/` | retrieveTask |
| PUT | `/api/internal/siftline/task/{uuid}/` | updateTask |
| PATCH | `/api/internal/siftline/task/{uuid}/` | partialUpdateTask |
| DELETE | `/api/internal/siftline/task/{uuid}/` | destroyTask |
| GET | `/api/internal/siftline/task/{uuid}/property/` | propertyTask |
| POST | `/api/internal/siftline/task/bulk-complete/` | bulkCompleteTask |
| POST | `/api/internal/siftline/task/create-by-group/` | createByGroupTask |
| POST | `/api/internal/siftline/task/create-by-preset/` | createByPresetTask |
| POST | `/api/internal/siftline/task/{uuid}/complete/` | completeTask |
| POST | `/api/internal/siftline/task/{uuid}/disable-recurrence/` | disableRecurrenceTask |
| POST | `/api/internal/siftline/task/{uuid}/enable-recurrence/` | enableRecurrenceTask |
| POST | `/api/internal/siftline/task/{uuid}/set-outcome/` | setOutcomeTask - Update appointment outcome (status, result, notes) |
| POST | `/api/internal/siftline/task/{uuid}/uncomplete/` | uncompleteTask |
| GET | `/api/internal/siftline/task-group/` | listTaskGroups |
| POST | `/api/internal/siftline/task-group/` | createTaskGroup |
| GET | `/api/internal/siftline/task-group/{uuid}/` | retrieveTaskGroup |
| PUT | `/api/internal/siftline/task-group/{uuid}/` | updateTaskGroup |
| PATCH | `/api/internal/siftline/task-group/{uuid}/` | partialUpdateTaskGroup |
| DELETE | `/api/internal/siftline/task-group/{uuid}/` | destroyTaskGroup |
| GET | `/api/internal/siftline/task-preset/` | listTaskPresets |
| POST | `/api/internal/siftline/task-preset/` | createTaskPreset |
| GET | `/api/internal/siftline/task-preset/{uuid}/` | retrieveTaskPreset |
| PUT | `/api/internal/siftline/task-preset/{uuid}/` | updateTaskPreset |
| PATCH | `/api/internal/siftline/task-preset/{uuid}/` | partialUpdateTaskPreset |
| DELETE | `/api/internal/siftline/task-preset/{uuid}/` | destroyTaskPreset |
| GET | `/api/internal/siftline/task-group/{task_group_uuid}/task-preset/` | listTaskPresets |
| POST | `/api/internal/siftline/task-group/{task_group_uuid}/task-preset/` | createTaskPreset |
| GET | `/api/internal/siftline/task-group/{task_group_uuid}/task-preset/{uuid}/` | retrieveTaskPreset |
| PUT | `/api/internal/siftline/task-group/{task_group_uuid}/task-preset/{uuid}/` | updateTaskPreset |
| PATCH | `/api/internal/siftline/task-group/{task_group_uuid}/task-preset/{uuid}/` | partialUpdateTaskPreset |
| DELETE | `/api/internal/siftline/task-group/{task_group_uuid}/task-preset/{uuid}/` | destroyTaskPreset |
| GET | `/api/internal/siftline/board/column/{column_uuid}/card/` | listCards |
| POST | `/api/internal/siftline/board/column/{column_uuid}/card/` | createCard |
| GET | `/api/internal/siftline/board/column/{column_uuid}/card/{uuid}/` | retrieveCard |
| PUT | `/api/internal/siftline/board/column/{column_uuid}/card/{uuid}/` | updateCard |
| PATCH | `/api/internal/siftline/board/column/{column_uuid}/card/{uuid}/` | partialUpdateCard |
| DELETE | `/api/internal/siftline/board/column/{column_uuid}/card/{uuid}/` | destroyCard |
| GET | `/api/internal/siftline/board/column/{column_uuid}/card/{uuid}/next/` | nextCard |
| GET | `/api/internal/siftline/board/column/{column_uuid}/card/{uuid}/prev/` | prevCard |
| GET | `/api/internal/siftline/board/column/{column_uuid}/card/{uuid}/timeline/` | timelineCard |

## Deals (6)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/deal/` | listDeals |
| POST | `/api/internal/deal/` | createDeal |
| GET | `/api/internal/deal/{uuid}/` | retrieveDeal |
| PUT | `/api/internal/deal/{uuid}/` | updateDeal |
| PATCH | `/api/internal/deal/{uuid}/` | partialUpdateDeal |
| DELETE | `/api/internal/deal/{uuid}/` | destroyDeal |

## Activity (7)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/activity/dataflik/` | listDataflikActivities |
| GET | `/api/internal/activity/dataflik/{uuid}/download-url/` | downloadUrlDataflikActivity |
| GET | `/api/internal/activity/skiptrace/chart/` | chartSkiptraceDashboardChart |
| GET | `/api/internal/activity/skiptrace/stats/` | statsSkiptraceDashboardStats |
| GET | `/api/internal/activity/` | listActivities |
| GET | `/api/internal/activity/{uuid}/export/` | exportActivity |
| GET | `/api/internal/activity/{uuid}/export-url/` | exportUrlActivity |

## Dashboards (3)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/dashboard/` | listDashboards |
| GET | `/api/internal/dashboard/{slug}/` | retrieveDashboard |
| GET | `/api/internal/dashboard-report/{uuid}/` | retrieveReport |

## Counties (2)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/county/` | listCounties |
| GET | `/api/internal/county/{fips}/` | retrieveCounty |

## DataFlik (23)

| Method | Path | Operation |
|---|---|---|
| GET | `/dataflik/search-account/` | listSearchAccountViewSets |
| GET | `/dataflik/account-info/{uuid}/` | retrieveAccount |
| GET | `/dataflik/upload/{attrs__dataflik_file_id}/` | retrieveUploadOutput |
| POST | `/dataflik/api-key/` | createApiKeyInput |
| POST | `/dataflik/api-key/disconnect/` | disconnectApiKeyInput |
| POST | `/dataflik/upload/` | createUploadOutput |
| GET | `/dataflik/property/` | listDataflikProperties |
| POST | `/dataflik/property/` | createDataflikProperty |
| GET | `/dataflik/property/{uuid}/` | retrieveDataflikProperty |
| PATCH | `/dataflik/property/{uuid}/` | partialUpdateDataflikProperty |
| DELETE | `/dataflik/property/{uuid}/` | destroyDataflikProperty |
| POST | `/dataflik/property/{uuid}/add-list/` | addListDataflikProperty |
| POST | `/dataflik/property/{uuid}/add-phone-status/` | addPhoneStatusDataflikProperty |
| POST | `/dataflik/property/{uuid}/add-phone-tag/` | addPhoneTagDataflikProperty |
| POST | `/dataflik/property/{uuid}/add-tag/` | addTagDataflikProperty |
| GET | `/dataflik/owner/` | listOwners |
| POST | `/dataflik/owner/` | createOwner |
| GET | `/dataflik/owner/{uuid}/` | retrieveOwner |
| PATCH | `/dataflik/owner/{uuid}/` | partialUpdateOwner |
| DELETE | `/dataflik/owner/{uuid}/` | destroyOwner |
| POST | `/dataflik/owner/{uuid}/add-phone-status/` | addPhoneStatusOwner |
| POST | `/dataflik/owner/{uuid}/add-phone-tag/` | addPhoneTagOwner |
| GET | `/dataflik/back-testing/{target_path}/` | getBackTestingDataFlikBackTestingViewSet - GET /api/dataflik/back-testing/... Forwards any GET /api/dataflik/back-testing/path:target_path/ to the external API. |

## Open API v1 Surface (top-level) (36)

| Method | Path | Operation |
|---|---|---|
| GET | `/property/` | listProperties |
| POST | `/property/` | createProperty |
| PATCH | `/property/` | partialUpdateProperty |
| DELETE | `/property/` | destroyProperty |
| GET | `/property/{uuid}/` | retrieveProperty |
| PATCH | `/property/{uuid}/` | partialUpdateProperty |
| DELETE | `/property/{uuid}/` | destroyProperty |
| POST | `/property/add-tag/` | addTagProperty |
| POST | `/property/add-list/` | addListProperty |
| POST | `/property/add-phone-tag/` | addPhoneTagProperty |
| POST | `/property/add-phone-status/` | addPhoneStatusProperty |
| POST | `/property/{uuid}/add-list/` | addListProperty |
| POST | `/property/{uuid}/add-phone-status/` | addPhoneStatusProperty |
| POST | `/property/{uuid}/add-phone-tag/` | addPhoneTagProperty |
| POST | `/property/{uuid}/add-tag/` | addTagProperty |
| GET | `/owner/` | listOwners |
| POST | `/owner/` | createOwner |
| PATCH | `/owner/` | partialUpdateOwner |
| DELETE | `/owner/` | destroyOwner |
| GET | `/owner/{uuid}/` | retrieveOwner |
| PATCH | `/owner/{uuid}/` | partialUpdateOwner |
| DELETE | `/owner/{uuid}/` | destroyOwner |
| POST | `/owner/add-phone-tag/` | addPhoneTagOwner |
| POST | `/owner/add-phone-status/` | addPhoneStatusOwner |
| POST | `/owner/{uuid}/add-phone-status/` | addPhoneStatusOwner |
| POST | `/owner/{uuid}/add-phone-tag/` | addPhoneTagOwner |
| GET | `/account/` | listAccounts |
| GET | `/account/{uuid}/` | retrieveAccount |
| GET | `/phone/` | listPhones |
| POST | `/phone/` | createPhone |
| GET | `/phone/{id}/` | retrievePhone |
| PUT | `/phone/{id}/` | updatePhone |
| PATCH | `/phone/{id}/` | partialUpdatePhone |
| DELETE | `/phone/{id}/` | destroyPhone |
| POST | `/phone/add-phone-status/` | addPhoneStatusPhone |
| POST | `/phone/add-phone-tag/` | addPhoneTagPhone |

## Dialer and SMS Integrations (71)

| Method | Path | Operation |
|---|---|---|
| GET | `/calltools/property/` | listProperties |
| POST | `/calltools/property/` | createProperty |
| GET | `/calltools/property/{uuid}/` | retrieveProperty |
| GET | `/launch-control/property/` | listProperties |
| POST | `/launch-control/property/` | createProperty |
| GET | `/launch-control/property/{uuid}/` | retrieveProperty |
| GET | `/reirail/property/` | listProperties |
| POST | `/reirail/property/` | createProperty |
| GET | `/reirail/property/{uuid}/` | retrieveProperty |
| GET | `/smarter-contact/property/` | listProperties |
| POST | `/smarter-contact/property/` | createProperty |
| GET | `/smarter-contact/property/{uuid}/` | retrieveProperty |
| GET | `/smarter-contact/owner/` | listOwners |
| GET | `/smarter-contact/owner/{uuid}/` | retrieveOwner |
| GET | `/smrtphone/property/` | listProperties |
| POST | `/smrtphone/property/` | createProperty |
| GET | `/smrtphone/property/{uuid}/` | retrieveProperty |
| GET | `/smrtphone/owner/` | listOwners |
| POST | `/smrtphone/owner/` | createOwner |
| GET | `/smrtphone/owner/{uuid}/` | retrieveOwner |
| PATCH | `/smrtphone/owner/{uuid}/` | partialUpdateOwner |
| POST | `/smrtphone/owner/{owner_uuid}/message/` | createMessage |
| GET | `/smrtdialer/property/` | listProperties |
| POST | `/smrtdialer/property/` | createProperty |
| GET | `/smrtdialer/property/{uuid}/` | retrieveProperty |
| GET | `/smrtdialer/owner/` | listOwners |
| POST | `/smrtdialer/owner/` | createOwner |
| GET | `/smrtdialer/owner/{uuid}/` | retrieveOwner |
| PATCH | `/smrtdialer/owner/{uuid}/` | partialUpdateOwner |
| POST | `/smrtdialer/owner/{owner_uuid}/message/` | createMessage |
| GET | `/plivo/sms/` | listSMSReceiveds |
| POST | `/plivo/sms/` | createSMSReceived |
| GET | `/plivo/sms/{id}/` | retrieveSMSReceived |
| PUT | `/plivo/sms/{id}/` | updateSMSReceived |
| PATCH | `/plivo/sms/{id}/` | partialUpdateSMSReceived |
| DELETE | `/plivo/sms/{id}/` | destroySMSReceived |
| GET | `/twilio/sms/` | listSMSReceiveds |
| POST | `/twilio/sms/` | createSMSReceived |
| GET | `/twilio/sms/{id}/` | retrieveSMSReceived |
| PUT | `/twilio/sms/{id}/` | updateSMSReceived |
| PATCH | `/twilio/sms/{id}/` | partialUpdateSMSReceived |
| DELETE | `/twilio/sms/{id}/` | destroySMSReceived |
| GET | `/xencall/property/` | listProperties |
| POST | `/xencall/property/` | createProperty |
| GET | `/xencall/property/{uuid}/` | retrieveProperty |
| GET | `/kixie/property/` | listProperties |
| POST | `/kixie/property/` | createProperty |
| GET | `/kixie/property/{uuid}/` | retrieveProperty |
| GET | `/kixie/owner/` | listOwners |
| GET | `/kixie/owner/{uuid}/` | retrieveOwner |
| POST | `/aircall/calls/` | createCallsViewSet |
| POST | `/aircall/calls/agent-declined/` | agentDeclinedCallsViewSet |
| POST | `/aircall/calls/answered/` | answeredCallsViewSet |
| POST | `/aircall/calls/ended/` | endedCallsViewSet |
| POST | `/aircall/calls/ringing-on-agent/` | ringingOnAgentCallsViewSet |
| POST | `/aircall/calls/voicemail-left/` | voicemailLeftCallsViewSet |
| POST | `/aircall/integrations/deleted/` | createIntegrationsViewSet |
| POST | `/smarter-contact/call/` | createCall |
| PATCH | `/smarter-contact/call/{uuid}/` | partialUpdateCall |
| POST | `/smrtphone/call/` | createCall |
| PATCH | `/smrtphone/call/{uuid}/` | partialUpdateCall |
| POST | `/smrtphone/sms/` | createSMS |
| POST | `/smrtphone/sms/update_status/` | updateStatusSMS |
| POST | `/smrtdialer/call/` | createCall |
| PATCH | `/smrtdialer/call/{uuid}/` | partialUpdateCall |
| POST | `/smrtdialer/sms/` | createSMS |
| POST | `/smrtdialer/sms/update_status/` | updateStatusSMS |
| POST | `/kixie/call/` | createCall |
| PATCH | `/kixie/call/{uuid}/` | partialUpdateCall |
| POST | `/kixie/sms/` | createSMS |
| POST | `/kixie/sms/update_status/` | updateStatusSMS |

## Email Integration (8)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/email-integration/` | listEmailIntegrations |
| POST | `/api/internal/email-integration/` | createCreateEmailIntegration |
| GET | `/api/internal/email-integration/{id}/` | retrieveEmailIntegration |
| PATCH | `/api/internal/email-integration/{id}/` | partialUpdateEmailIntegration |
| DELETE | `/api/internal/email-integration/{id}/` | destroyEmailIntegration |
| GET | `/api/internal/email-integration/{email_integration_uuid}/read-email/` | listReadEmailViewSets |
| GET | `/api/internal/email-integration/{email_integration_uuid}/get-attachment/{message_id}/{attachment_id}/` | listReadEmailAttachmentViewSets |
| POST | `/api/internal/email-integration/{email_integration_uuid}/send-email/` | createSendEmail |

## Calendar Integration (7)

| Method | Path | Operation |
|---|---|---|
| GET | `/api/internal/calendar-integration/` | listCalendarIntegrations |
| POST | `/api/internal/calendar-integration/` | createCreateCalendarIntegration |
| GET | `/api/internal/calendar-integration/{uuid}/` | retrieveCalendarIntegration |
| PATCH | `/api/internal/calendar-integration/{uuid}/` | partialUpdateCalendarIntegration |
| DELETE | `/api/internal/calendar-integration/{uuid}/` | destroyCalendarIntegration |
| GET | `/api/internal/calendar-integration/{calendar_integration_uuid}/events/` | listListEventsQueries |
| POST | `/api/internal/calendar-integration/{calendar_integration_uuid}/events/` | createCreateEvent |

## Webhook Admin (2)

| Method | Path | Operation |
|---|---|---|
| GET | `/admin/accounts/integration/{id}/twilio_detail/set_webhook/{sid}` | setWebhookTwilioViewSet |
| GET | `/admin/accounts/integration/{id}/plivo_detail/set_webhook/{sid}` | setWebhookPlivoViewSet |

## Health Check (1)

| Method | Path | Operation |
|---|---|---|
| GET | `/health-check/` | listhealth_check_views |

